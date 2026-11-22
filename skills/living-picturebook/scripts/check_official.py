#!/usr/bin/env python3
"""把 skills 产线的产物对着官方要求过一遍，给出「能不能交」的结论。

检查项全部来自官方材料（docs/纸书智伴.pdf 第 3–5 页 + 答疑 Q1–Q43）：
    Q26/Q39 时长 100–200 s（安全区 100–180）
    Q26     ≥720P、≥24 fps
    Q33     正文每页都要在成片里
    Q43     同一册一次生成、不能二次编辑
    Q11     视频内检索四能力需要时间轴
    Q28/Q35 触控到独立语义对象级（需要 spots）
    Q4      两段视频：非交互成片 + 交互操作录屏
    Q9/Q34  语音首响 2 s

只读产物、不调模型、不花钱。退出码：0=可交，1=有阻断项，2=只有缺口没有阻断。

用法：
    python check_official.py --plan out/scene_plan.json --pages-dir out/pages \
        --book books/cn01 --out out/official_check.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image


def load_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="skills 产物 × 官方要求核对")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--pages-dir", required=True)
    ap.add_argument("--book", default="", help="如 books/cn01（用于取页数/语言/spots）")
    ap.add_argument("--timeline", default="", help="page_timeline.json（默认 plan 同目录）")
    ap.add_argument("--movie", default="", help="成片路径（默认 plan 同目录/final_movie.mp4）")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    plan_path = Path(a.plan)
    plan = load_json(plan_path) or {}
    out_dir = plan_path.parent
    segs = plan.get("segments") or []
    pages = plan.get("pages") or []

    results = []

    def add(item: str, official: str, status: str, detail: str) -> None:
        results.append({"item": item, "official": official, "status": status, "detail": detail})

    # 1 时长
    total = sum(int(s.get("duration_sec") or 0) for s in segs)
    ok_len = 100 <= total <= 200
    add("总时长", "Q26/Q39：100–200 s（安全区 100–180）", "PASS" if ok_len else "FAIL",
        f"实际 {total}s / {len(segs)} 段")

    # 2 页覆盖
    book = load_json(Path(a.book) / "reader" / "book.json") if a.book else None
    book_pages = (book or {}).get("pages") or []
    plan_pages = {p.get("page_id") for p in pages if p.get("page_id")}
    if book_pages:
        missing = [p.get("id") for p in book_pages if p.get("id") not in plan_pages]
        add("正文页覆盖", "Q33：正文每页都要在成片里", "PASS" if not missing else "FAIL",
            f"书 {len(book_pages)} 页 / 分镜 {len(plan_pages)} 页" + (f"；缺 {missing}" if missing else ""))
    else:
        add("正文页覆盖", "Q33", "UNKNOWN", "没给 --book，无法对照页数")

    # 3 分辨率与一致性
    sizes = []
    for pdir in sorted(Path(a.pages_dir).iterdir()):
        kf = pdir / "clean_keyframe.png"
        if kf.is_file():
            with Image.open(kf) as im:
                sizes.append(im.size)
    if sizes:
        min_side = min(min(w, h) for w, h in sizes)
        uniform = len(set(sizes)) == 1
        add("分辨率", "Q26：≥720P", "PASS" if min_side >= 720 else "FAIL",
            f"关键帧短边最小 {min_side}px；{len(set(sizes))} 种尺寸")
        add("关键帧一致性", "双锚定要求同尺寸", "PASS" if uniform else "WARN",
            f"尺寸集合 {sorted(set(sizes))[:3]}")
    else:
        add("分辨率", "Q26", "UNKNOWN", f"{a.pages_dir} 下没有 clean_keyframe.png")

    # 4 帧率
    add("帧率", "Q26：≥24 fps", "UNKNOWN", "帧率由 Seedance 输出决定，需生成后用 ffprobe 复核")

    # 5 时间轴
    tl_path = Path(a.timeline) if a.timeline else out_dir / "page_timeline.json"
    tl = load_json(tl_path)
    if tl:
        add("页时间码", "Q11：跳帧/定位需要时间轴", "PASS",
            f"{tl.get('page_count')} 页 / {tl.get('segment_count')} 段 / 总 {tl.get('total_sec')}s")
    else:
        add("页时间码", "Q11", "WARN", "没有 page_timeline.json（只跑到分镜时正常）")

    # 6 触控 spots
    spots = sum(len(p.get("spots") or []) for p in book_pages)
    add("触控对象（spots）", "Q28/Q35：独立语义对象级、点击暂停并介绍",
        "PASS" if spots else "FAIL",
        f"book.json 里有 {spots} 个 spots；skills 产线本身不产出/不消费 spots，"
        f"需要交互层从 book.json 取" if spots else "没有任何 spots")

    # 7 两段视频
    movie = Path(a.movie) if a.movie else out_dir / "final_movie.mp4"
    add("非交互成片", "Q4 ①", "PASS" if movie.is_file() else "WARN",
        f"{movie}" if movie.is_file() else "尚未生成（dry-run 不出片）")
    add("交互操作录屏", "Q4 ②；Q39：≤5 min", "FAIL",
        "skills 产线不产出；要另外录浏览器操作，且录屏必须一镜到底")

    # 8 Q43
    if len(segs) > 1:
        add("一次生成（Q43）", "同一册必须一次生成、不能二次编辑", "BLOCK",
            f"当前是 {len(segs)} 段分别生成再 concat，按严格口径属于二次编辑；"
            f"要么先与组委会确认边界，要么改用单趟渲染（book2real/storyfilm.py）")
    else:
        add("一次生成（Q43）", "Q43", "PASS", "只有一段")

    # 9 语音
    add("语音问答", "Q9/Q34：首响 2 s；Q11：正确率按 Query 计", "FAIL",
        "skills 产线不含 ASR/TTS/问答；由 backend/ 负责，需单独验收")

    # 汇总
    blocking = [r for r in results if r["status"] in ("FAIL", "BLOCK")]
    verdict = "可交" if not blocking else ("不可交（有阻断）" if any(r["status"] == "BLOCK" for r in results)
                                           else "不可交（有缺口）")

    report = {"plan": str(plan_path), "verdict": verdict, "total_sec": total,
              "segments": len(segs), "results": results}
    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for r in results:
        print(f"[{r['status']:<5}] {r['item']:<12} {r['detail']}")
    print(f"\n结论：{verdict}")
    has_block = any(r["status"] == "BLOCK" for r in results)
    return 0 if not blocking else (1 if has_block else 2)


if __name__ == "__main__":
    sys.exit(main())
