#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""book_timeline.py — 把「段」翻译成交互层能用的「页 → 时间码」。

背景（为什么需要这个脚本）：
    整本绘本的视频是**一次生成、拼成一条、之后不可再切**的。交互层能拿到的
    只有一个播放进度 t（秒）和一个暂停动作。它不知道此刻画面上是第几页，
    也就无法让 LLM 回答"这在绘本里是什么"。

    所以视频侧必须额外交付一张 `page_timeline.json`：
        page_003 → [12.0, 17.0]
    暂停时按 t 查表 → 得到 page_id → 交互层再拿官方元素清单去问 LLM。

输入：
    - scene_plan.json（picturebook-scene-director 产出）：段、段内页、计划时长
    - clips 目录（可缺）：out/seg_###/clip.mp4，有则用 ffprobe 取真实时长
输出：
    - page_timeline.json / page_timeline.md

只依赖标准库；ffprobe 缺失时自动退回"用计划时长"，并在 checks 里标出来。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

TIMELINE_VERSION = "1.0.0"
PLAN_TOLERANCE_SEC = 0.45  # 与 relay qc 的结构门禁保持一致


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_text_lf(path: Path, text: str) -> None:
    """Windows 上必须显式 newline=''，否则 \\n 会被写成 \\r\\n，落盘后 sha 对不上。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def ffprobe_duration(clip: Path) -> Optional[float]:
    """返回视频时长（秒）。ffprobe 不存在或失败时返回 None。"""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        return round(float(out.stdout.strip()), 3)
    except ValueError:
        return None


def find_clip(clips_dir: Optional[Path], segment_id: str) -> Optional[Path]:
    if clips_dir is None:
        return None
    for cand in (clips_dir / segment_id / "clip.mp4",
                 clips_dir / f"{segment_id}.mp4"):
        if cand.is_file():
            return cand
    return None


def split_pages_evenly(pages: List[str], start: float, duration: float) -> List[Dict[str, Any]]:
    """段内页边界只能估：一镜到底的段里没有硬边界。

    MERGED_PAGES 段（一页以上）按页数均分，并标 estimated=True，让交互层知道
    这个边界是"估计"而不是"剪辑点"。
    """
    n = max(1, len(pages))
    step = duration / n
    out = []
    for i, page in enumerate(pages):
        out.append({
            "page_id": page,
            "start_sec": round(start + i * step, 3),
            "end_sec": round(start + (i + 1) * step, 3),
            "estimated": n > 1,
        })
    return out


def build_timeline(plan: Dict[str, Any], clips_dir: Optional[Path]) -> Dict[str, Any]:
    segments = plan.get("segments") or []
    if not segments:
        raise SystemExit("[book_timeline] scene_plan 里没有 segments，无法计时")

    concat = {c.get("after_segment"): c for c in (plan.get("concat_plan") or [])}
    warnings: List[str] = []
    seg_rows: List[Dict[str, Any]] = []
    page_rows: List[Dict[str, Any]] = []

    cursor = 0.0
    measured_any = False
    max_delta = 0.0

    for idx, seg in enumerate(segments):
        seg_id = seg.get("segment_id") or f"seg_{idx + 1:03d}"
        planned = float(seg.get("duration_sec") or 5.0)
        clip = find_clip(clips_dir, seg_id)
        actual = ffprobe_duration(clip) if clip else None
        if actual is not None:
            measured_any = True
            delta = abs(actual - planned)
            max_delta = max(max_delta, delta)
            if delta > PLAN_TOLERANCE_SEC:
                warnings.append(
                    f"{seg_id} 实际时长 {actual}s 与计划 {planned}s 相差 {delta:.2f}s "
                    f"> {PLAN_TOLERANCE_SEC}s，时间码已按实际值计算"
                )
            duration = actual
            source = "ffprobe"
        else:
            duration = planned
            source = "scene_plan"
            if clips_dir is not None:
                warnings.append(f"{seg_id} 找不到 clip.mp4，退回计划时长 {planned}s")

        start = cursor
        end = round(start + duration, 3)
        pages = seg.get("pages") or []
        seg_rows.append({
            "segment_id": seg_id,
            "pages": pages,
            "start_sec": round(start, 3),
            "end_sec": end,
            "duration_sec": round(duration, 3),
            "duration_source": source,
            "clip": str(clip) if clip else None,
            "segment_kind": seg.get("segment_kind"),
        })
        for row in split_pages_evenly(pages, start, duration):
            row["segment_id"] = seg_id
            page_rows.append(row)

        # 下一段起点：concat_plan 若声明了叠化，两段会重叠 xfade_sec
        nxt = concat.get(seg_id) or {}
        xfade = float(nxt.get("xfade_sec") or 0.0) if nxt.get("cut") == "xfade" else 0.0
        if xfade > 0:
            warnings.append(
                f"{seg_id} 之后有 {xfade}s 叠化：整本一条片里叠化等于两段重叠，"
                f"时间码按重叠后的实际起点计算"
            )
        cursor = end - xfade

    total = round(seg_rows[-1]["end_sec"] if seg_rows else 0.0, 3)
    page_rows.sort(key=lambda r: r["start_sec"])

    # 重叠校验：页区间不该互相压盖
    for a, b in zip(page_rows, page_rows[1:]):
        if b["start_sec"] < a["end_sec"] - 0.001:
            warnings.append(f"{a['page_id']} 与 {b['page_id']} 在时间轴上重叠，检查 concat_plan")

    return {
        "timeline_version": TIMELINE_VERSION,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "mode": "measured" if measured_any else "plan",
        "master_ratio": plan.get("master_ratio"),
        "page_count": len(page_rows),
        "segment_count": len(seg_rows),
        "total_sec": total,
        "segments": seg_rows,
        "pages": page_rows,
        "checks": {
            "durations_source": "ffprobe+scene_plan" if measured_any else "scene_plan",
            "plan_tolerance_sec": PLAN_TOLERANCE_SEC,
            "max_abs_delta_sec": round(max_delta, 3),
            "warnings": warnings,
        },
        "interaction_contract": {
            "lookup": "按 t 在 pages[] 里二分查找，取 start_sec <= t < end_sec 的那一页",
            "pause_event": "暂停即查表，不需要视频侧发任何事件",
            "estimated_pages": [r["page_id"] for r in page_rows if r.get("estimated")],
            "rule": "本文件是视频侧唯一交付给交互层的时间契约；视频不可切、不可重生成",
        },
    }


def page_at(timeline: Dict[str, Any], t: float) -> Optional[Dict[str, Any]]:
    hit = None
    for row in timeline["pages"]:
        if row["start_sec"] <= t < row["end_sec"]:
            hit = row
    if hit is None and timeline["pages"] and t >= timeline["pages"][-1]["end_sec"]:
        hit = timeline["pages"][-1]
    return hit


def render_md(tl: Dict[str, Any]) -> str:
    lines = [
        "# 页 → 时间码（交互层契约）",
        "",
        f"- 生成时间：{tl['generated_at']}",
        f"- 计时口径：`{tl['mode']}`（measured = 用真实片段时长；plan = 用分镜计划时长）",
        f"- 画幅：{tl['master_ratio']} ｜ 总时长：{tl['total_sec']}s ｜ 段 {tl['segment_count']} · 页 {tl['page_count']}",
        "",
        "## 页表",
        "",
        "| 页 | 所属段 | 起 | 止 | 边界是否估计 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in tl["pages"]:
        lines.append(
            f"| {r['page_id']} | {r['segment_id']} | {r['start_sec']}s | {r['end_sec']}s | "
            f"{'是（合页段均分）' if r.get('estimated') else '否（段首）'} |"
        )
    lines += ["", "## 段表", "", "| 段 | 含页 | 起 | 止 | 时长来源 | clip |",
              "| --- | --- | --- | --- | --- | --- |"]
    for r in tl["segments"]:
        lines.append(
            f"| {r['segment_id']} | {', '.join(r['pages']) or '—'} | {r['start_sec']}s | "
            f"{r['end_sec']}s | {r['duration_source']} | {'有' if r['clip'] else '无'} |"
        )
    w = tl["checks"]["warnings"]
    lines += ["", "## 警告", ""]
    lines += [f"- {x}" for x in w] if w else ["- 无"]
    lines += [
        "",
        "## 交互层怎么用",
        "",
        "1. 读者暂停 → 读 `video.currentTime` → 在本表 `pages[]` 里二分查找 → 得到 `page_id`。",
        "2. 用 `page_id` + 点击坐标去查官方元素清单，再把「第几页 + 哪个元素」交给 LLM。",
        "3. 不需要视频侧发 `landed` 之类的事件：视频是一条不可切的片子，没有「翻页瞬间」。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="把 scene_plan 转成交互层用的 page_timeline.json")
    ap.add_argument("--plan", required=True, type=Path, help="scene_plan.json")
    ap.add_argument("--clips-dir", type=Path, default=None, help="out/ 目录（含 seg_###/clip.mp4），可缺")
    ap.add_argument("--out-dir", type=Path, default=Path("book_out"), help="输出目录")
    ap.add_argument("--at", type=float, default=None, help="查询某个时刻属于哪一页，然后退出")
    args = ap.parse_args(argv)

    if not args.plan.is_file():
        print(f"[book_timeline] 找不到 scene_plan: {args.plan}", file=sys.stderr)
        return 2
    plan = load_json(args.plan)
    timeline = build_timeline(plan, args.clips_dir)

    if args.at is not None:
        row = page_at(timeline, args.at)
        if row is None:
            print(f"[book_timeline] t={args.at}s 不在任何页区间内（total {timeline['total_sec']}s）")
            return 1
        print(f"t={args.at}s -> {row['page_id']} [{row['start_sec']}, {row['end_sec']})"
              f"{'  (estimated)' if row.get('estimated') else ''}")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_text_lf(args.out_dir / "page_timeline.json",
                  json.dumps(timeline, ensure_ascii=False, indent=2) + "\n")
    write_text_lf(args.out_dir / "page_timeline.md", render_md(timeline))

    print(f"[book_timeline] {timeline['page_count']} 页 / {timeline['segment_count']} 段 / "
          f"{timeline['total_sec']}s ({timeline['mode']})")
    for r in timeline["pages"]:
        flag = " *" if r.get("estimated") else ""
        print(f"  {r['page_id']:<12} {r['start_sec']:>7.2f} → {r['end_sec']:>7.2f}{flag}")
    for w in timeline["checks"]["warnings"]:
        print(f"  [warn] {w}")
    print(f"[book_timeline] -> {args.out_dir / 'page_timeline.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
