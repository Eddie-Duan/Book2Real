#!/usr/bin/env python3
"""按文本把绘本页聚成场景，输出 scene_plan.json（官方 Q33：跨 2–3 页可合并）。

判据只有四条，全部可解释：
  1. 续页不切：上一页文字没有句末标点，这页必须同场
  2. 硬边界：本页开头出现时间/地点转换词
  3. 角色全换：两页角色集合完全不重叠
  4. 场长上限 3 页（超过必切）

用法：
    python group_scenes.py --book books/cn01 --out .tmp/scene_plan.json
    python group_scenes.py --book books/cn01 --out .tmp/scene_plan.json --pages-dir .tmp/skill_pages
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 时间/地点转换词（中英）
TIME_PLACE_CUES = [
    "第二天", "次日", "第二天早上", "第二天下午", "第二天晚上",
    "后来", "过后", "过了一会儿", "过了一会", "过了一会儿", "此时", "这时",
    "突然", "于是", "然后", "接着", "到了", "回到家", "回到家后", "回到", "来到",
    "走进", "离开", "晚上", "早上", "中午", "下午", "天黑", "第二天一早",
    "从此", "最后", "终于",
    "meanwhile", "later", "next day", "the next morning", "the next day",
    "suddenly", "then", "after that", "at last", "finally", "that night",
    "the following day", "in the morning", "at night",
]

# 句末标点（没有就视为续页）
TERMINAL = "。！？.!?…」』”\"'）)"

MAX_PAGES_PER_SCENE = 4      # 一场最多 4 页
MAX_PAGES_PER_SHOT = 2      # 一个镜头最多覆盖 2 页（官方 Q33：跨 2–3 页可合并）


def norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def chars_of(page: dict) -> set:
    out = set()
    for c in page.get("characters") or []:
        if isinstance(c, dict):
            v = c.get("id") or c.get("name")
        else:
            v = c
        if v:
            out.add(str(v))
    return out


def starts_with_cue(text: str) -> str | None:
    head = norm(text)[:14]
    for cue in TIME_PLACE_CUES:
        if head.startswith(norm(cue)):
            return cue
    return None


def ends_terminal(text: str) -> bool:
    t = (text or "").rstrip()
    return bool(t) and t[-1] in TERMINAL


def boundary_score(prev: dict | None, cur: dict, scene_len: int) -> tuple[int, list[str]]:
    """返回 (分数, 信号)。分数 >= 1 就切。"""
    signals: list[str] = []
    score = 0
    text = cur.get("text") or ""

    if prev is None:
        return 10, ["第一页"]

    # 规则 1：续页不切（句子没写完，必须先留在同一场）
    continuing = bool((prev.get("text") or "").strip()) and not ends_terminal(prev.get("text"))
    if continuing:
        signals.append("上一页句子没结束（续页）")

    # 规则 4：场长上限（硬规则）——满 3 页必切；只有续页且未满 4 页才放行
    if scene_len >= MAX_PAGES_PER_SCENE:
        # 只有「刚好到上限前一页且句子未完」才宽限一页，把场长封在 MAX 页
        if continuing and scene_len == MAX_PAGES_PER_SCENE - 1:
            signals.append(f"场长 {scene_len} 页但句子未完，宽限到 {MAX_PAGES_PER_SCENE} 页")
        else:
            signals.append(f"场长已达上限 {MAX_PAGES_PER_SCENE} 页")
            return 10, signals

    if continuing:
        score -= 3

    # 规则 2：硬边界
    cue = starts_with_cue(text)
    if cue:
        signals.append(f"时间/地点转换词:{cue}")
        score += 2

    # 规则 3：角色全换
    c_prev, c_cur = chars_of(prev), chars_of(cur)
    if c_prev and c_cur and not (c_prev & c_cur):
        signals.append(f"角色全换 {sorted(c_prev)} -> {sorted(c_cur)}")
        score += 1

    # 空文本页：不新开场景
    if not norm(text):
        signals.append("空文本页，挂到当前场")
        score -= 2

    return score, signals


def plan_shots(pages: list[dict]) -> list[dict]:
    """每页一镜，时长后面统一抬档（见 fit_duration）。"""
    return [{"kind": "single", "pages": [p["page_id"]], "duration_sec": 5} for p in pages]


def fit_duration(scenes: list[dict], target: int = 120, lo: int = 100, hi: int = 200) -> None:
    """把总时长抬进官方区间：每镜 5→10 s；抬够之后再考虑无损合并同场相邻页。"""
    shots = [sh for sc in scenes for sh in sc["shot_plan"]]

    def total() -> int:
        return sum(sh["duration_sec"] for sh in shots)

    for sh in shots:
        if total() >= target:
            break
        sh["duration_sec"] = 10

    # 无损合并：同场相邻两页若都已是 10 s，合起来仍是 10 s，省一次调用
    for sc in scenes:
        if len(sc["shot_plan"]) < 2:
            continue
        i = 0
        merged = []
        while i < len(sc["shot_plan"]):
            cur = sc["shot_plan"][i]
            nxt = sc["shot_plan"][i + 1] if i + 1 < len(sc["shot_plan"]) else None
            if (nxt and cur["duration_sec"] == 10 and nxt["duration_sec"] == 10
                    and total() - 10 >= lo):
                merged.append({"kind": "merged", "pages": cur["pages"] + nxt["pages"],
                               "duration_sec": 10})
                shots.remove(nxt)
                i += 2
            else:
                merged.append(cur)
                i += 1
        sc["shot_plan"] = merged

    if total() < lo:
        print(f"[grouper] 警告：最多只能到 {total()}s，低于官方下限 {lo}s；需要拆更多镜头",
              file=sys.stderr)
    if total() > hi:
        print(f"[grouper] 警告：{total()}s 超过上限 {hi}s", file=sys.stderr)


def transition_between(prev_scene: dict, next_scene: dict) -> dict:
    a = set(prev_scene["character_set"])
    b = set(next_scene["character_set"])
    if a and b and (a & b):
        return {"type": "MATCH_CUT", "bridge": "none",
                "reason": f"角色有交集 {sorted(a & b)}"}
    if not a or not b:
        # 没有角色信息：用两页文字的 2-gram 重叠兜底
        t1 = "".join(p["text"] for p in prev_scene["pages"][-1:])
        t2 = "".join(p["text"] for p in next_scene["pages"][:1])
        g1, g2 = bigrams(t1), bigrams(t2)
        if g1 and g2:
            j = len(g1 & g2) / len(g1 | g2)
            if j >= 0.25:
                return {"type": "MATCH_CUT", "bridge": "none",
                        "reason": f"无角色信息；文字 2-gram 重叠 {j:.2f}"}
    return {"type": "DESIGNED_TRANSITION", "bridge": "black",
            "reason": "角色无交集 / 跨场"}


def load_page_characters(book_dir: Path) -> dict:
    """从 reader/characters/characters.json 取「角色 -> 出场页」，补页面上的 characters 字段。"""
    f = Path(book_dir) / "reader" / "characters" / "characters.json"
    out: dict[str, set] = {}
    if not f.is_file():
        return out
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return out
    chars = data.get("characters") if isinstance(data, dict) else data
    for c in chars or []:
        cid = c.get("id") or c.get("name")
        for pid in c.get("pages") or []:
            out.setdefault(str(pid), set()).add(str(cid))
    return out


def bigrams(text: str) -> set:
    t = norm(text)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else set()


def build(book: dict, page_chars: dict | None = None) -> dict:
    page_chars = page_chars or {}
    pages = []
    for i, p in enumerate(book.get("pages") or []):
        pid = p.get("id") or f"p{i+1:02d}"
        chars = set(chars_of(p)) | set(page_chars.get(pid, set()))
        pages.append({
            "page_id": pid,
            "text": p.get("text") or "",
            "character_set": sorted(chars),
            "scene_hint": p.get("scene") or "",
        })

    if not pages:
        raise SystemExit("[grouper] book.json 里没有 pages")

    scenes: list[dict] = []
    boundaries: list[dict] = []
    cur: dict | None = None

    for idx, page in enumerate(pages):
        prev = pages[idx - 1] if idx else None
        scene_len = len(cur["pages"]) if cur else 0
        score, signals = boundary_score(prev, page, scene_len)

        if cur is None or score >= 1:
            if cur is not None:
                boundaries.append({"at": page["page_id"], "score": score, "signals": signals})
                cur["transition_out"] = None  # 稍后补
            cur = {"pages": [page], "signals": signals if idx else ["第一页"]}
            scenes.append(cur)
        else:
            cur["pages"].append(page)

    for si, sc in enumerate(scenes, start=1):
        sc["scene_id"] = f"sc_{si:02d}"
        ids = [p["page_id"] for p in sc["pages"]]
        sc["page_ids"] = ids
        sc["text_chars"] = sum(len(norm(p["text"])) for p in sc["pages"])
        sc["character_set"] = sorted({c for p in sc["pages"] for c in p["character_set"]})
        sc["shot_plan"] = plan_shots(sc["pages"])

    fit_duration(scenes)

    for sc in scenes:
        sc["est_seconds"] = sum(s["duration_sec"] for s in sc["shot_plan"])

    for si, sc in enumerate(scenes[:-1]):
        sc["transition_out"] = transition_between(sc, scenes[si + 1])
    scenes[-1]["transition_out"] = {"type": "END", "bridge": "none", "reason": "片尾"}

    total_chars = sum(len(norm(p["text"])) for p in pages)
    assigned_chars = sum(sc["text_chars"] for sc in scenes)

    plan = {
        "plan_version": "1.0.0",
        "source": "picturebook-scene-grouper (text-first)",
        "book_id": book.get("book_id"),
        "lang": (book.get("language") or "zh").lower(),
        "page_count": len(pages),
        "scene_count": len(scenes),
        "max_pages_per_scene": MAX_PAGES_PER_SCENE,
        "segments": [
            {
                "segment_id": sc["scene_id"],
                "pages": sc["page_ids"],
                "duration_sec": sc["est_seconds"],
                "shot_plan": sc["shot_plan"],
                "character_ids": sc["character_set"],
                "transition_out": sc["transition_out"],
            }
            for sc in scenes
        ],
        "coverage": {
            "pages_total": len(pages),
            "pages_assigned": len(pages),
            "text_chars_total": total_chars,
            "text_chars_assigned": assigned_chars,
            "text_pct": round(assigned_chars / total_chars, 4) if total_chars else 1.0,
            "pages_pct": 1.0,
        },
        "boundaries": boundaries,
        "estimated_total_sec": sum(sc["est_seconds"] for sc in scenes),
    }
    return plan


def apply_scene_tags(plan: dict, pages_dir: Path) -> int:
    """把 scene_id 写回 page_manifest.json，给 director 的语义轴用。"""
    page_to_scene = {}
    for sc in plan["segments"]:
        for pid in sc["pages"]:
            page_to_scene[pid] = sc["segment_id"]
    n = 0
    for mf in Path(pages_dir).glob("*/page_manifest.json"):
        d = json.loads(mf.read_text(encoding="utf-8"))
        pid = d.get("page_id") or mf.parent.name
        if pid in page_to_scene:
            d["scene_tag"] = page_to_scene[pid]
            mf.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="按文本把页聚成场景")
    ap.add_argument("--book", required=True, help="如 books/cn01")
    ap.add_argument("--out", required=True, help="scene_plan.json 输出路径")
    ap.add_argument("--pages-dir", default="", help="可选：把 scene_tag 写回 page_manifest.json")
    a = ap.parse_args()

    book_path = Path(a.book) / "reader" / "book.json"
    if not book_path.is_file():
        book_path = Path(a.book) / "book.json"
    book = json.loads(book_path.read_text(encoding="utf-8"))

    plan = build(book, load_page_characters(Path(a.book)))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if a.pages_dir:
        n = apply_scene_tags(plan, Path(a.pages_dir))
        print(f"[grouper] scene_tag 写回 {n} 个 page_manifest")

    print(f"[grouper] {plan['page_count']} 页 -> {plan['scene_count']} 场，"
          f"预估 {plan['estimated_total_sec']}s，文字覆盖 {plan['coverage']['text_pct']:.1%}")
    for sc in plan["segments"]:
        tr = sc["transition_out"]["type"]
        print(f"  {sc['segment_id']}  {'+'.join(sc['pages']):<22} {sc['duration_sec']:>2}s  "
              f"{len(sc['pages'])} 页  ->{tr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
