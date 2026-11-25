#!/usr/bin/env python3
"""Build a reader timeline from a one_take shot_script.

Per-shot mode: shots carry page and targets[{name, box}] so click boxes follow
the camera. Fallback: page-level even split.

Usage:
  python scripts/one_take_timeline_shots.py --book books/cn01 \
      --run runs/cn01/one_take --movie runs/cn01/one_take/final_movie.mp4 \
      --publish-root runs/cn01/seedance_film
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

EXTRA_TEXT = {
    "cn01": {"p06": [{"text": "我又不是故意的！", "score": 0.99,
                       "box": [0.262, 0.468, 0.486, 0.556]}]}
}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def probe(movie):
    exe = shutil.which("ffprobe")
    if not exe:
        return 1280, 720, 24.0, 0.0
    out = subprocess.run([exe, "-v", "error", "-print_format", "json",
                          "-show_streams", "-show_format", str(movie)],
                         capture_output=True, text=True, timeout=120)
    data = json.loads(out.stdout or "{}")
    w = h = 0
    fps = 24.0
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            w, h = int(s.get("width") or 0), int(s.get("height") or 0)
            num, _, den = str(s.get("r_frame_rate") or "24/1").partition("/")
            fps = round(float(num) / float(den or 1), 3)
    dur = float(((data.get("format") or {}).get("duration") or 0))
    return w or 1280, h or 720, fps, dur


def page_parts(book_id, pid, pages):
    page = pages.get(pid) or {}
    spots = []
    for s in page.get("spots") or []:
        if isinstance(s, dict) and isinstance(s.get("box"), (list, tuple)):
            b = [float(v) for v in s["box"]]
            spots.append({"name": str(s.get("name") or ""), "desc": str(s.get("desc") or ""),
                          "box": [round(b[0], 4), round(b[1], 4), round(b[2], 4), round(b[3], 4)]})
    ocr = [dict(x) for x in (EXTRA_TEXT.get(book_id, {}).get(pid) or [])]
    for line in page.get("ocr_lines") or []:
        if isinstance(line, dict) and isinstance(line.get("box"), (list, tuple)):
            b = [float(v) for v in line["box"]]
            sw = float(page.get("source_w") or 1)
            sh = float(page.get("source_h") or 1)
            ocr.append({"text": str(line.get("text") or ""), "score": line.get("score"),
                        "box": [round(b[0] / sw, 4), round(b[1] / sh, 4),
                                round(b[2] / sw, 4), round(b[3] / sh, 4)]})
    return page, spots, ocr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--movie", type=Path, default=None)
    ap.add_argument("--publish-root", type=Path, default=None)
    a = ap.parse_args()
    movie = a.movie or (a.run / "preview_150s.mp4")
    if not movie.is_file():
        raise SystemExit(f"missing movie {movie}")
    script = load(a.run / "shot_script.json")
    reader = load(a.book / "reader" / "book.json")
    book = load(a.book / "book.json") if (a.book / "book.json").is_file() else {}
    pages = {str(p.get("id")): p for p in reader.get("pages") or [] if isinstance(p, dict)}
    book_id = str(book.get("book_id") or a.book.name)
    width, height, fps, duration = probe(movie)

    segs = []
    if "p01" in pages:
        p0 = pages["p01"]
        segs.append({"kind": "cover", "id": "p01", "source_page": p0.get("source_page"),
                     "segment_id": "cover", "t0": 0.0, "t1": 0.001,
                     "art_rect": [0, 0, width, height], "art": p0.get("art") or "",
                     "source_w": 1, "source_h": 1, "mapping": "identity",
                     "text": str(p0.get("text") or ""), "scene": str(p0.get("scene") or ""),
                     "spots": p0.get("spots") or [], "characters": p0.get("characters") or [],
                     "ocr_lines": []})
    cursor = 0.0
    for chunk in script.get("chunks") or []:
        dur = float(chunk.get("duration") or 0)
        shots = [s for s in chunk.get("shots") or [] if isinstance(s, dict)]
        wanted = [str(p) for p in chunk.get("pages") or []]
        if not shots:
            step = dur / max(1, len(wanted))
            for i, pid in enumerate(wanted):
                page, spots, ocr = page_parts(book_id, pid, pages)
                segs.append({"kind": "page", "id": pid, "source_page": page.get("source_page"),
                             "segment_id": chunk.get("id"), "t0": round(cursor + i * step, 3),
                             "t1": round(cursor + (i + 1) * step, 3),
                             "art_rect": [0, 0, width, height], "art": page.get("art") or "",
                             "source_w": 1, "source_h": 1, "mapping": "identity",
                             "text": str(page.get("text") or ""), "scene": str(page.get("scene") or ""),
                             "spots": spots, "characters": page.get("characters") or [],
                             "ocr_lines": ocr})
            cursor += dur
            continue
        acc = 0.0
        for i, shot in enumerate(shots):
            sd = float(shot.get("duration") or 0)
            pid = str(shot.get("page") or (wanted[min(i, len(wanted) - 1)] if wanted else ""))
            if not pid:
                acc += sd
                continue
            page, spots, ocr = page_parts(book_id, pid, pages)
            targets = shot.get("targets") or []
            if targets:
                by_name = {s["name"]: s for s in spots}
                spots = []
                for t in targets:
                    if not isinstance(t, dict) or not isinstance(t.get("box"), (list, tuple)):
                        continue
                    b = [float(v) for v in t["box"]]
                    old = by_name.get(t.get("name")) or {}
                    spots.append({"name": str(t.get("name") or ""),
                                  "desc": str(t.get("desc") or old.get("desc") or ""),
                                  "box": [round(b[0], 4), round(b[1], 4),
                                          round(b[2], 4), round(b[3], 4)]})
            t0 = cursor + acc
            t1 = cursor + (acc + (sd if sd > 0 else dur))
            segs.append({"kind": "page", "id": pid, "source_page": page.get("source_page"),
                         "segment_id": chunk.get("id"), "shot_id": shot.get("id"),
                         "t0": round(t0, 3), "t1": round(t1, 3),
                         "art_rect": [0, 0, width, height], "art": page.get("art") or "",
                         "source_w": 1, "source_h": 1, "mapping": "identity",
                         "text": str(page.get("text") or ""), "scene": str(page.get("scene") or ""),
                         "spots": spots, "characters": page.get("characters") or [],
                         "ocr_lines": ocr})
            acc += sd
        cursor += dur

    tl = {"book_id": book_id, "video": "film.mp4", "width": width, "height": height,
          "fps": fps, "duration": round(segs[-1]["t1"] if segs else 0.0, 3),
          "language": book.get("language") or "zh",
          "title": book.get("title") or a.book.name, "source": "one_take_shots",
          "segments": segs}
    out = a.run / "timeline.json"
    out.write_text(json.dumps(tl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[shots] {len(segs)} segs / {tl['duration']}s / "
          f"{sum(len(s['spots']) for s in segs)} spots / "
          f"{sum(len(s['ocr_lines']) for s in segs)} ocr")
    if a.publish_root:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        d = a.publish_root / stamp
        d.mkdir(parents=True, exist_ok=True)
        target = d / "film.mp4"
        if target.exists():
            target.unlink()
        try:
            os.link(movie, target)
        except Exception:
            shutil.copyfile(movie, target)
        (d / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
        (d / "meta.json").write_text(json.dumps(
            {"source": "one_take_shots", "movie": str(movie), "published_at": stamp},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[shots] published -> {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
