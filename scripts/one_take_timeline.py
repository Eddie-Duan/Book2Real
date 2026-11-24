#!/usr/bin/env python3
"""把 one_take 成片（shot_script + 成片）接成播放器可读的一档。

one_take 是按 30s 大块生成的（一块含 1–3 页），时间戳只到块级；交互层要的是
**页级 [t0,t1) + spots/text/ocr**，所以这里按"块时长 ÷ 块内页数"切页，并把
reader/book.json 里的文本、可点元素、OCR 行挂上去。

注意（必须写在报告里）：one_take 的画面是重新生成的，机位与页图构图不一致，
页内归一化 box 只能当"大致方位"用；要精确到"点得到"，得在生成时记下每个元素的
目标框（我们自己的 Seedance 段路径靠静态机位 + 关键帧做到了这点）。

用法：
    python scripts/one_take_timeline.py --book books/cn01 \
        --run runs/cn01/one_take --publish-root runs/cn01/seedance_film
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def run_ffprobe(clip: Path) -> dict:
    import subprocess

    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, "-v", "error", "-print_format", "json",
                              "-show_streams", "-show_format", str(clip)],
                             capture_output=True, text=True, timeout=120)
        return json.loads(out.stdout or "{}")
    except Exception:
        return {}


def validate_reader_timeline(items: list) -> list:
    problems = []
    if not items:
        return ["没有生成任何页级段"]
    tl = items[0]
    prev = -1.0
    for seg in tl.get("segments") or []:
        if seg["t1"] <= seg["t0"]:
            problems.append(f"{seg['id']} 时间区间非正")
        if seg["t0"] < prev - 0.001:
            problems.append(f"{seg['id']} 与前一段重叠")
        prev = max(prev, seg["t1"])
        for spot in seg.get("spots") or []:
            x, y, w, h = spot["box"]
            if w <= 0 or h <= 0:
                problems.append(f"{seg['id']} spot {spot['name']!r} 框为 0")
            if any(v < -0.001 or v > 1.001 for v in (x, y, x + w, y + h)):
                problems.append(f"{seg['id']} spot {spot['name']!r} 越界")
    return problems


def build(book_dir: Path, script: dict, width: int, height: int, fps: float) -> dict:
    reader = load_json(book_dir / "reader" / "book.json")
    pages = {str(p.get("id")): p for p in (reader.get("pages") or []) if isinstance(p, dict)}
    book = load_json(book_dir / "book.json") if (book_dir / "book.json").is_file() else {}

    segments: list[dict] = []
    cursor = 0.0
    for chunk in script.get("chunks") or []:
        dur = float(chunk.get("duration") or 0)
        wanted = [str(p) for p in (chunk.get("pages") or [])]
        if not wanted or dur <= 0:
            cursor += dur
            continue
        step = dur / len(wanted)
        for i, pid in enumerate(wanted):
            page = pages.get(pid) or {}
            spots = []
            for spot in page.get("spots") or []:
                if not isinstance(spot, dict) or not isinstance(spot.get("box"), (list, tuple)):
                    continue
                b = [float(v) for v in spot["box"]]
                spots.append({"name": str(spot.get("name") or ""),
                              "desc": str(spot.get("desc") or ""),
                              "box": [round(b[0], 4), round(b[1], 4),
                                      round(b[2], 4), round(b[3], 4)]})
            ocr = []
            for line in page.get("ocr_lines") or []:
                if not isinstance(line, dict) or not isinstance(line.get("box"), (list, tuple)):
                    continue
                b = [float(v) for v in line["box"]]
                sw = float(page.get("source_w") or 1)
                sh = float(page.get("source_h") or 1)
                ocr.append({"text": str(line.get("text") or ""), "score": line.get("score"),
                            "box": [round(b[0] / sw, 4), round(b[1] / sh, 4),
                                    round(b[2] / sw, 4), round(b[3] / sh, 4)]})
            t0 = round(cursor + i * step, 3)
            segments.append({
                "kind": "page", "id": pid,
                "source_page": page.get("source_page"),
                "segment_id": chunk.get("id"),
                "t0": t0, "t1": round(t0 + step, 3),
                "art_rect": [0, 0, width, height],
                "art": page.get("art") or "",
                "art_w": page.get("art_w"), "art_h": page.get("art_h"),
                "source_w": 1, "source_h": 1,
                "text": str(page.get("text") or ""),
                "scene": str(page.get("scene") or ""),
                "spots": spots,
                "characters": page.get("characters") or [],
                "ocr_lines": ocr,
            })
        cursor += dur

    return {
        "book_id": str(book.get("book_id") or book_dir.name),
        "video": "film.mp4", "width": width, "height": height, "fps": fps,
        "duration": round(segments[-1]["t1"] if segments else 0.0, 3),
        "language": book.get("language") or "zh",
        "title": book.get("title") or book_dir.name,
        "source": "one_take",
        "segments": segments,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="one_take 成片 -> 播放器 timeline")
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True, help="runs/<id>/one_take")
    ap.add_argument("--movie", type=Path, default=None, help="默认 <run>/preview_150s.mp4")
    ap.add_argument("--publish-root", type=Path, default=None,
                    help="如 runs/<id>/seedance_film；给了就落 film.mp4 + timeline.json")
    a = ap.parse_args()

    script_path = a.run / "shot_script.json"
    script = load_json(script_path)
    movie = a.movie or (a.run / "preview_150s.mp4")
    if not movie.is_file():
        raise SystemExit(f"[one_take] 找不到成片 {movie}")

    info = run_ffprobe(movie)
    width = height = 0
    fps = 24.0
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            width, height = int(s.get("width") or 0), int(s.get("height") or 0)
            num, _, den = str(s.get("r_frame_rate") or "24/1").partition("/")
            fps = round(float(num) / float(den or 1), 3)
    duration = float(((info.get("format") or {}).get("duration") or 0))

    tl = build(a.book, script, width or 1280, height or 720, fps)
    problems = validate_reader_timeline([tl])
    out = a.run / "timeline.json"
    out.write_text(json.dumps(tl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    covered = [s["id"] for s in tl["segments"]]
    spots = sum(len(s["spots"]) for s in tl["segments"])
    ocr = sum(len(s["ocr_lines"]) for s in tl["segments"])
    print(f"[one_take] 页级段 {len(tl['segments'])}：{covered}")
    print(f"[one_take] 时长 {tl['duration']}s（成片 {round(duration, 3)}s）/ "
          f"{tl['width']}x{tl['height']}@{tl['fps']}fps / 可点 {spots} / OCR {ocr}")
    for p in problems:
        print(f"  [warn] {p}")
    print(f"[one_take] -> {out}")

    if a.publish_root:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = a.publish_root / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / "film.mp4"
        if target.exists():
            target.unlink()
        try:
            os.link(movie, target)          # 同盘硬链，150MB 不复制
        except Exception:
            shutil.copyfile(movie, target)
        (run_dir / "timeline.json").write_text(
            json.dumps(tl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (run_dir / "meta.json").write_text(json.dumps({
            "source": "one_take", "movie": str(movie), "publish_root": str(a.publish_root),
            "published_at": stamp, "box_note": "页内框仅近似：one_take 画面重绘，机位与页图不一致",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[one_take] 已发布 -> {run_dir}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
