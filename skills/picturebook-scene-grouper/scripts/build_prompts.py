#!/usr/bin/env python3
"""把 scene_plan.json 变成 prompts.json（每个场景一条正式提示词契约）。

契约字段（官方要求的生成输入，缺一不可）：
    scene_id / pages / first_frame / duration_sec / prompt / negative

- first_frame：该场景第一页的清字关键帧（图生视频的首帧）
- prompt：风格 DNA + 该场景正文（按页顺序拼接，去掉换行）+ 无文字约束
- negative：明确禁止项（文字/字幕/水印/多余角色/写实照片）
- duration_sec：场景内所有镜头的时长之和

用法：
    python build_prompts.py --book books/cn01 --scene-plan .tmp/scene_plan_cn01.json \
        --pages-dir .tmp/skill_pages --out .tmp/prompts_cn01.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_STYLE_DNA = (
    "儿童绘本插画风格；严格保持原书的画风、笔触、配色与光线；"
    "构图与主体比例沿用参考图；镜头平稳，适合 5–10 秒短片。"
)
DEFAULT_NEGATIVE = (
    "画面中出现文字, 字幕, 音效字, 水印, logo, 台标, 边框, 多余人物, "
    "现代物品, 写实照片, 文字水印, 变形, 扭曲, 闪烁"
)
PROMPT_CHAR_LIMIT = 1000


def load_text_by_page(book: dict) -> dict:
    return {p.get("id"): (p.get("text") or "").strip() for p in (book.get("pages") or [])}


def main() -> int:
    ap = argparse.ArgumentParser(description="scene_plan -> prompts.json")
    ap.add_argument("--book", required=True)
    ap.add_argument("--scene-plan", required=True)
    ap.add_argument("--pages-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--style-dna", default=DEFAULT_STYLE_DNA)
    ap.add_argument("--negative", default=DEFAULT_NEGATIVE)
    a = ap.parse_args()

    book_path = Path(a.book) / "reader" / "book.json"
    if not book_path.is_file():
        book_path = Path(a.book) / "book.json"
    book = json.loads(book_path.read_text(encoding="utf-8"))
    texts = load_text_by_page(book)
    plan = json.loads(Path(a.scene_plan).read_text(encoding="utf-8"))
    pages_dir = Path(a.pages_dir)

    rows = []
    problems = []
    for seg in plan.get("segments") or []:
        sid = seg.get("segment_id")
        pages = seg.get("pages") or []
        if not pages:
            problems.append(f"{sid} 没有页")
            continue
        kf = pages_dir / pages[0] / "clean_keyframe.png"
        if not kf.is_file():
            problems.append(f"{sid} 缺首帧 {kf}")
            continue
        body = " ".join(texts.get(pid, "") for pid in pages).replace("\n", " ").strip()
        if not body:
            problems.append(f"{sid} 正文为空，提示词缺内容")
        prompt = (f"{a.style_dna}\n本场景画面：{body}\n"
                  f"画面本身不要出现任何文字；保持角色一致；镜头平稳。")[:PROMPT_CHAR_LIMIT]
        shots = seg.get("shot_plan") or []
        dur = sum(int(s.get("duration_sec") or 0) for s in shots) or int(seg.get("duration_sec") or 0)
        rows.append({
            "scene_id": sid,
            "pages": pages,
            "first_frame": str(kf),
            "duration_sec": dur,
            "shots": [{"kind": s.get("kind"), "pages": s.get("pages"),
                       "duration_sec": s.get("duration_sec")} for s in shots],
            "prompt": prompt,
            "negative": a.negative,
        })

    out = {
        "book_id": book.get("book_id") or Path(a.book).name,
        "lang": (book.get("language") or "zh").lower(),
        "style_dna": a.style_dna,
        "scene_count": len(rows),
        "total_sec": sum(r["duration_sec"] for r in rows),
        "segments": rows,
        "problems": problems,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[prompts] {out['scene_count']} 场 / {out['total_sec']}s -> {a.out}")
    for r in rows:
        print(f"  {r['scene_id']}  {r['duration_sec']:>2}s  {'+'.join(r['pages'])}  "
              f"prompt {len(r['prompt'])} 字")
    for p in problems:
        print(f"  [warn] {p}")
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())
