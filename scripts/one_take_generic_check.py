#!/usr/bin/env python3
"""泛化验收（不花钱）：六本官方书都要能走完 one-take 的"非生成"链路。

只验证不调模型的部分：
  1) build_bible：页/角色/场景/道具齐全，目标时长落在 100–180
  2) plate_specs：有配置读配置，没配置也能按场景合成 plate（不是只有 cn01/en01 能跑）
  3) planner 的 user prompt：包含全部页、包含风格卡（若有）、默认走 LLM 而不是示例
  4) 代码里不再有按书名写死的分支（CN01_SPECS / PROPS_BY_BOOK / CHAR_EXTRA_BY_BOOK 等）

跑法：python scripts/one_take_generic_check.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "picturebook-one-take" / "scripts"))

import assets  # noqa: E402
import bible as bible_mod  # noqa: E402
import planner  # noqa: E402

FORBIDDEN_NAMES = ("CN01_SPECS", "EN01_SPECS", "PROPS_BY_BOOK", "LOCATIONS_BY_BOOK",
                   "CHAR_EXTRA_BY_BOOK", "CHAR_DEFAULTS")


def check_code_generic() -> list[str]:
    problems = []
    for name in ("bible.py", "assets.py", "planner.py"):
        src = (REPO / "skills" / "picturebook-one-take" / "scripts" / name).read_text(encoding="utf-8")
        for token in FORBIDDEN_NAMES:
            if re.search(rf"\b{token}\b", src):
                problems.append(f"{name}: 仍有写死的表 {token}")
        # 允许 docstring/注释里提 cn01，但代码分支里不许有
        for m in re.finditer(r'(?:==|\.endswith\(|\.startswith\()\s*"?(cn0\d|en0\d)', src):
            problems.append(f"{name}: 还有按书名写死的分支 {m.group(0)!r}")
    return problems


def check_book(book_dir: Path) -> dict:
    bid = book_dir.name
    row = {"book": bid, "ok": True, "notes": []}
    try:
        b = bible_mod.build_bible(str(book_dir))
    except Exception as exc:  # noqa: BLE001
        return {**row, "ok": False, "notes": [f"bible 失败: {exc}"]}

    pages = b.get("pages") or []
    chars = b.get("characters") or []
    locs = b.get("locations") or []
    row.update(pages=len(pages), characters=len(chars), locations=len(locs),
               props=len(b.get("props") or []), target=b.get("target_seconds"),
               configured=(book_dir / "one_take" / "bible.json").is_file())
    if len(pages) < 5:
        row["ok"] = False
        row["notes"].append("页数过少")
    if not (100 <= int(b.get("target_seconds") or 0) <= 180):
        row["ok"] = False
        row["notes"].append("目标时长不在 100–180")
    if not chars:
        row["notes"].append("无角色（数据缺口：reader 缺 characters.json / curation 无 spots）")
    if not locs:
        row["ok"] = False
        row["notes"].append("推不出场景")

    specs = assets.plate_specs(b)
    row["plates"] = len(specs)
    if not specs:
        row["ok"] = False
        row["notes"].append("没有可生成的 plate 规格")
    for name, spec in specs.items():
        if not spec.get("prompt"):
            row["ok"] = False
            row["notes"].append(f"plate {name} 缺 prompt")

    # prompt 里必须出现所有页 id，且风格卡（若有）要拼进去
    style_path = book_dir / "style_card.json"
    style_text = ""
    if style_path.is_file():
        style_text = planner._style_block(planner.jread(style_path))
        row["style"] = len(style_text)
    prompt = planner._user_prompt(b, int(b.get("target_seconds") or 150), style_text)
    missing = [p["id"] for p in pages if p.get("id") and p["id"] not in prompt]
    if missing:
        row["ok"] = False
        row["notes"].append(f"prompt 漏页 {missing[:5]}")
    if style_text and style_text not in prompt:
        row["ok"] = False
        row["notes"].append("风格卡没进 prompt")
    row["prompt_chars"] = len(prompt)
    return row


def main() -> int:
    books = sorted(p for p in (REPO / "books").iterdir()
                   if (p / "reader" / "book.json").is_file())
    print(f"[generic] 检查 {len(books)} 本书（不调任何模型）\n")
    rows = [check_book(b) for b in books]
    for r in rows:
        flag = "PASS" if r["ok"] else "FAIL"
        print(f"{flag} {r['book']:<6} 页{r.get('pages', '-'):>3} 角色{r.get('characters', '-'):>2} "
              f"场景{r.get('locations', '-'):>2} 道具{r.get('props', '-'):>2} "
              f"plate{r.get('plates', '-'):>2} prompt{r.get('prompt_chars', '-'):>5}字 "
              f"卡{r.get('style', 0):>4} | 配置{'有' if r.get('configured') else '无'}")
        for n in r.get("notes") or []:
            print(f"      - {n}")

    code_problems = check_code_generic()
    if code_problems:
        print("\n[generic] 代码里仍有写死的地方：")
        for p in code_problems:
            print("  -", p)

    # 默认路径必须是 LLM（示例只能显式点名）——纯静态判定，绝不发请求
    import inspect

    default_ok = True
    kwargs = inspect.signature(planner.plan).parameters
    if kwargs.get("use_llm") is None or kwargs["use_llm"].default is not True:
        default_ok = False
        print("\n[generic] FAIL：plan() 的 use_llm 默认不是 True")
    if kwargs.get("from_example") is None or kwargs["from_example"].default is not False:
        default_ok = False
        print("\n[generic] FAIL：from_example 默认不是 False")
    src = (REPO / "skills" / "picturebook-one-take" / "scripts" / "planner.py").read_text(encoding="utf-8")
    if ".endswith(\"cn01\")" in src or 'endswith("cn01")' in src:
        default_ok = False
        print("\n[generic] FAIL：planner 里还有 cn01 默认捷径")

    ok = all(r["ok"] for r in rows) and not code_problems and default_ok
    summary = {"books": rows, "code_problems": code_problems, "default_uses_llm": default_ok,
               "all_ok": ok}
    out = REPO / "books" / "one_take_generic_check.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[generic] {'全部通过' if ok else '有失败项'} -> {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
