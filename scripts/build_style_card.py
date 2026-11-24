#!/usr/bin/env python3
"""把 skill4 的"风格卡"导出成数据文件：books/<id>/style_card.json。

为什么是"导出数据"而不是"引用模块"：
    backend/ 是上游 paperbook-backend 的 vendored 镜像，下次同步会整目录覆盖。
    skills/ 下的 planner 如果直接 import backend.app.skill4，一次上游同步就静默断掉。
    所以这里一次性把卡算好落盘，planner 只读 JSON。

产物：
    books/<id>/style_card.json     {card..., "prompt_preview": ...}
        card       = skill4.build_style_card(reader_book)
        prompt     = skill4.style_prompt(card, answer_lang=书的语言, mode="qa")

用法：
    python scripts/build_style_card.py                 # 全部书
    python scripts/build_style_card.py --books books/cn01 books/en01
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _skill4():
    """延迟导入 backend 的 skill4（纯 Python，不调模型）。"""
    backend = REPO / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.skill4 import build_style_card, style_prompt  # noqa: PLC0415

    return build_style_card, style_prompt


def book_dirs(explicit: list[Path] | None) -> list[Path]:
    if explicit:
        return [p.resolve() for p in explicit]
    return sorted(p for p in (REPO / "books").iterdir()
                  if (p / "reader" / "book.json").is_file())


def build_one(book_dir: Path, force: bool = False) -> dict:
    build_style_card, style_prompt = _skill4()
    reader = json.loads((book_dir / "reader" / "book.json").read_text(encoding="utf-8"))
    card = build_style_card(reader)
    lang = card.get("lang") or "zh"
    prompt = style_prompt(card, answer_lang=lang, mode="qa")
    out = {
        "book_id": reader.get("book_id") or book_dir.name,
        **card,
        "prompt_preview": prompt,
        "source": "backend/app/skill4.build_style_card",
    }
    target = book_dir / "style_card.json"
    if target.is_file() and not force:
        try:
            old = json.loads(target.read_text(encoding="utf-8"))
            if old.get("prompt_preview") == prompt:
                out["unchanged"] = True
        except Exception:
            pass
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"book": out["book_id"], "lang": lang, "samples": len(card.get("samples") or []),
            "avg_len": card.get("avg_sentence_len"), "unit": card.get("unit"),
            "pov": card.get("pov"), "path": str(target),
            "unchanged": bool(out.get("unchanged"))}


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 skill4 风格卡")
    ap.add_argument("--books", nargs="*", type=Path, default=None)
    ap.add_argument("--force", action="store_true", help="即使内容相同也重写")
    a = ap.parse_args()

    rows = []
    for book_dir in book_dirs(a.books):
        try:
            rows.append(build_one(book_dir, force=a.force))
        except Exception as exc:  # noqa: BLE001
            rows.append({"book": book_dir.name, "error": str(exc)})

    for r in rows:
        if r.get("error"):
            print(f"[style] {r['book']}: FAIL {r['error']}")
        else:
            print(f"[style] {r['book']}: lang={r['lang']} pov={r['pov']} "
                  f"样本 {r['samples']} 句 / 平均 {r['avg_len']}{r['unit']}"
                  f"{' (未变)' if r['unchanged'] else ''} -> {r['path']}")
    summary = REPO / "books" / "style_cards_summary.json"
    summary.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[style] -> {summary}")
    return 0 if not any(r.get("error") for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
