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


def run_ffprobe(clip: Path) -> Dict[str, Any]:
    """完整 ffprobe JSON（规格用）；失败返回空壳，调用方自己兜底。"""
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(clip)],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        return json.loads(out.stdout or "{}")
    except Exception:
        return {}


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


def _clip_spec(clips_dir: Optional[Path], segments: list) -> tuple[int, int, float]:
    """成片规格：优先 ffprobe 第一段，否则 1920x1080/24fps（720p 也够用，坐标是归一化的）。"""
    for seg in segments:
        clip = find_clip(clips_dir, str(seg.get("segment_id") or ""))
        if clip is None:
            continue
        try:
            info = run_ffprobe(clip)
        except Exception:
            continue
        for s in info.get("streams", []):
            if s.get("codec_type") == "video":
                num, _, den = str(s.get("r_frame_rate") or "24/1").partition("/")
                try:
                    fps = float(num) / float(den or 1)
                except Exception:
                    fps = 24.0
                return int(s.get("width") or 1920), int(s.get("height") or 1080), round(fps, 3)
    return 1920, 1080, 24.0


def page_norm_to_frame(nb: list, qa: dict) -> list:
    """页内归一化框 -> 成片帧归一化框。

    Seedance 段的首帧就是 ``clean_keyframe.png``（机位锁死静态），所以映射是一条
    仿射：页图 → 裁掉纸白边 → 补到目标画幅 → 缩放到关键帧。关键帧就是视频首帧，
    因此"帧归一化"= "关键帧归一化"。
    """
    checks = (qa or {}).get("checks") or {}
    crop = checks.get("crop_box")
    src = checks.get("source_size")
    kf = checks.get("keyframe_size")
    x, y, w, h = [float(v) for v in (nb or [0, 0, 0, 0])]
    if not (crop and src and kf) or len(crop) != 4 or len(src) != 2 or len(kf) != 2:
        return [x, y, x + w, y + h]
    sx, sy = float(src[0]), float(src[1])
    cx0, cy0, cx1, cy1 = [float(v) for v in crop]
    cw, ch = max(1.0, cx1 - cx0), max(1.0, cy1 - cy0)
    kw, kh = float(kf[0]), float(kf[1])
    ratio = kw / kh
    if cw / ch >= ratio:
        pw, ph = cw, cw / ratio
    else:
        pw, ph = ch * ratio, ch
    offx, offy = (pw - cw) / 2.0, (ph - ch) / 2.0
    scale = kw / pw

    def one(u: float, v: float) -> tuple[float, float]:
        px, py = u * sx - cx0 + offx, v * sy - cy0 + offy
        return max(0.0, min(1.0, (px * scale) / kw)), max(0.0, min(1.0, (py * scale) / kh))

    x0, y0 = one(x, y)
    x1, y1 = one(x + w, y + h)
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def build_reader_segments(plan: dict, clips_dir: Optional[Path], book_dir: Optional[Path],
                          pages_dir: Optional[Path]) -> list[dict]:
    """把分镜段翻成 reader（film.js）认的段：t0/t1 + spots/text/ocr/art_rect。

    没有这一步，Seedance 成片接进播放器后点选/查字/描述全是空转——不是模型问题，
    是时间码没带几何与文本。
    """
    if not book_dir or not pages_dir:
        return []
    reader_path = book_dir / "reader" / "book.json"
    if not reader_path.is_file():
        reader_path = book_dir / "book.json"
    if not reader_path.is_file():
        return []
    reader = load_json(reader_path)
    pages = {str(p.get("id")): p for p in (reader.get("pages") or []) if isinstance(p, dict)}
    book = load_json(book_dir / "book.json") if (book_dir / "book.json").is_file() else {}

    out: list[dict] = []
    cursor = 0.0
    concat = {c.get("after_segment"): c for c in (plan.get("concat_plan") or [])}
    for idx, seg in enumerate(plan.get("segments") or []):
        seg_id = str(seg.get("segment_id") or f"seg_{idx + 1:03d}")
        planned = float(seg.get("duration_sec") or 5.0)
        clip = find_clip(clips_dir, seg_id)
        actual = ffprobe_duration(clip) if clip else None
        duration = float(actual) if actual is not None else planned
        pages_in_seg = [str(p) for p in (seg.get("pages") or [])]
        slices = split_pages_evenly(pages_in_seg, cursor, duration)
        for row in slices:
            pid = str(row.get("page_id"))
            page = pages.get(pid) or {}
            qa = {}
            qa_path = Path(pages_dir) / pid / "qa_report.json"
            if qa_path.is_file():
                try:
                    qa = load_json(qa_path)
                except Exception:
                    qa = {}
            spots = []
            for spot in page.get("spots") or []:
                if not isinstance(spot, dict) or not isinstance(spot.get("box"), (list, tuple)):
                    continue
                nb = page_norm_to_frame(list(spot["box"]), qa)
                spots.append({"name": str(spot.get("name") or ""),
                              "desc": str(spot.get("desc") or ""),
                              # film.js 的 spotNorm 认 [x, y, w, h]（页内归一化）
                              "box": [round(nb[0], 4), round(nb[1], 4),
                                      round(nb[2] - nb[0], 4), round(nb[3] - nb[1], 4)]})
            ocr = []
            for line in page.get("ocr_lines") or []:
                if not isinstance(line, dict) or not isinstance(line.get("box"), (list, tuple)):
                    continue
                b = [float(v) for v in line["box"]]
                # OCR 框是源图像素坐标 -> 归一化 -> 帧归一化
                if qa.get("checks", {}).get("source_size"):
                    sw, sh = [float(v) for v in qa["checks"]["source_size"]]
                else:
                    sw, sh = float(page.get("source_w") or 1), float(page.get("source_h") or 1)
                nb = page_norm_to_frame([b[0] / sw, b[1] / sh, (b[2] - b[0]) / sw, (b[3] - b[1]) / sh], qa)
                # film.js 的 ocrNorm 认 [x0,y0,x1,y1] 源图像素；这里给归一化框 + source=1
                ocr.append({"text": str(line.get("text") or ""), "score": line.get("score"),
                            "box": [round(nb[0], 4), round(nb[1], 4),
                                    round(nb[2], 4), round(nb[3], 4)]})
            out.append({
                "kind": "page",
                "id": pid,
                "source_page": page.get("source_page"),
                "segment_id": seg_id,
                "t0": round(row.get("start_sec", 0.0), 3),
                "t1": round(row.get("end_sec", 0.0), 3),
                "art_rect": [0, 0, 0, 0],       # 下面按成片规格填
                "source_w": 1, "source_h": 1,   # OCR 框已归一化：normBox 除 1 即通过
                "art": page.get("art") or "",
                "art_w": page.get("art_w"),
                "art_h": page.get("art_h"),
                "text": str(page.get("text") or ""),
                "scene": str(page.get("scene") or ""),
                "spots": spots,
                "characters": page.get("characters") or [],
                "ocr_lines": ocr,
            })
        nxt = concat.get(seg_id) or {}
        xfade = float(nxt.get("xfade_sec") or 0.0) if nxt.get("cut") == "xfade" else 0.0
        cursor += duration - xfade

    width, height, fps = _clip_spec(clips_dir, plan.get("segments") or [])
    # 叠化段在成片里是两段重叠 0.6s：播放器只认 [t0,t1) 单一归属，所以把上一页的
    # 结束时间贴到下一页的起点。交叉溶解期间的归属取后一页（观众看到的是新画面浮出）。
    for a, b in zip(out, out[1:]):
        if b["t0"] >= a["t0"]:
            a["t1"] = b["t0"]
    for row in out:
        row["art_rect"] = [0, 0, width, height]
    total = round(out[-1]["t1"] if out else 0.0, 3)
    return [{"book_id": str(book.get("book_id") or (book_dir.name if book_dir else "")),
             "video": "film.mp4", "width": width, "height": height, "fps": fps,
             "duration": total, "language": (book.get("language") or "zh"),
             "title": book.get("title") or book.get("book_id") or "",
             "segments": out}]


def validate_reader_timeline(items: list[dict]) -> list[str]:
    """film.js 契约校验：时间不重叠、box 归一到 [0,1] 且非零、art_rect 有尺寸。"""
    problems: list[str] = []
    if not items:
        return ["没有生成任何 reader 段（缺 --book/--pages-dir，或 book 里没有 pages）"]
    tl = items[0]
    if not (tl.get("width") and tl.get("height")):
        problems.append("缺成片规格 width/height")
    prev_end = -1.0
    for seg in tl["segments"]:
        if seg["t1"] <= seg["t0"]:
            problems.append(f"{seg['id']} 时间区间非正：{seg['t0']}~{seg['t1']}")
        if seg["t0"] < prev_end - 0.001:
            problems.append(f"{seg['id']} 与前一段时间重叠")
        prev_end = max(prev_end, seg["t1"])
        rect = seg.get("art_rect") or []
        if len(rect) != 4 or not rect[2] or not rect[3]:
            problems.append(f"{seg['id']} art_rect 无效")
        for spot in seg.get("spots") or []:
            x, y, w, h = spot["box"]
            if w <= 0 or h <= 0:
                problems.append(f"{seg['id']} spot {spot['name']!r} 框宽高为 0")
            if any(v < -0.001 or v > 1.001 for v in (x, y, x + w, y + h)):
                problems.append(f"{seg['id']} spot {spot['name']!r} 越界 {spot['box']}")
    return problems


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
    ap.add_argument("--book", type=Path, default=None,
                    help="书目录（books/<id>）；给了就额外产出 reader 版时间码（spots/text/ocr）")
    ap.add_argument("--pages-dir", type=Path, default=None,
                    help="页目录（含 page_###/qa_report.json 的 crop_box），坐标换算用")
    ap.add_argument("--out-dir", type=Path, default=Path("book_out"), help="输出目录")
    ap.add_argument("--movie", type=Path, default=None,
                    help="Seedance 成片（final_movie.mp4）；配合 --publish-root 落成播放器可读的一档")
    ap.add_argument("--publish-root", type=Path, default=None,
                    help="发布根目录，如 runs/cn01/seedance_film；会新建 <时间戳>/ 放 film.mp4 + timeline.json")
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

    # reader 版（Seedance 成片接播放器要的那份：t0/t1 + spots/text/ocr + art_rect）
    if args.book or args.pages_dir:
        items = build_reader_segments(plan, args.clips_dir, args.book, args.pages_dir)
        problems = validate_reader_timeline(items)
        if items:
            write_text_lf(args.out_dir / "reader_timeline.json",
                          json.dumps(items[0], ensure_ascii=False, indent=2) + "\n")
            tl = items[0]
            spot_n = sum(len(s.get("spots") or []) for s in tl["segments"])
            print(f"[book_timeline] reader 时间码：{len(tl['segments'])} 段 / "
                  f"{spot_n} 个可点元素 / {tl['width']}x{tl['height']} @{tl['fps']}fps")
        for p in problems:
            print(f"  [reader-warn] {p}")
        if problems:
            return 1
        if args.publish_root:
            if not args.movie or not args.movie.is_file():
                print("[book_timeline] --publish-root 需要 --movie 指向真实成片", file=sys.stderr)
                return 2
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            run_dir = args.publish_root / stamp
            run_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.movie, run_dir / "film.mp4")
            (run_dir / "timeline.json").write_text(
                json.dumps(items[0], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (run_dir / "meta.json").write_text(json.dumps({
                "source": "seedance", "movie": str(args.movie), "plan": str(args.plan),
                "published_at": stamp,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[book_timeline] 已发布 -> {run_dir}（film.mp4 + timeline.json）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
