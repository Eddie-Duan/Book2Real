"""Build the character wall (portraits) from the *cast* agents, not by hand.

    python scripts/build_characters.py --book books/charlie_lola

Wraps the existing cast chain and then, for each character, makes sure the
portrait keeps the whole head: a 4:5 crop that has a detected face outside its
box is widened instead of shipped. Faces are detected with OpenCV's Haar
cascade when available; a painted face that Haar misses is left as is (Haar is a
reject signal here, never a naming signal).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _common  # noqa: F401
import numpy as np
from PIL import Image

from book2real.cast.pipeline import run as run_cast
from book2real.util import imread, read_json
from book2real.vision import faces as face_mod


def _pad_to_45(crop: Image.Image, fill=(250, 247, 240)) -> Image.Image:
    w, h = crop.size
    if w / h > 0.8:
        nh = int(w / 0.8)
        canvas = Image.new("RGB", (w, nh), fill)
        canvas.paste(crop, (0, (nh - h) // 2))
        return canvas
    nw = int(h * 0.8)
    canvas = Image.new("RGB", (w if nw < w else nw, h), fill)
    canvas.paste(crop, ((canvas.width - w) // 2, 0))
    return canvas


def fix_portrait(reader: Path, rel: str, report: dict) -> str:
    path = reader / rel
    if not path.exists():
        return "missing"
    rgb = imread(path)
    h, w = rgb.shape[:2]
    dets = face_mod.faces(rgb)
    if not dets:
        report["no_face"] = True
        return "no face detected (kept)"
    for fx, fy, fw, fh in dets:
        cx, cy = fx + fw / 2, fy + fh / 2
        margin = 0.03
        if not (margin * w <= cx <= (1 - margin) * w and margin * h <= cy <= (1 - margin) * h):
            # a face touches the border: grow the crop outward from its centre
            scale = 1.35
            nw, nh = int(w * scale), int(h * scale)
            big = Image.fromarray(rgb).resize((nw, nh), Image.LANCZOS)
            left = max(0, int(cx * scale - w / 2))
            top = max(0, int(cy * scale - h / 2))
            big = big.crop((left, top, left + w, top + h))
            big.save(path, quality=94)
            report["widened"] = True
            return "widened"
    report["ok"] = True
    return "ok (face inside)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--models", action="store_true")
    ap.add_argument("--no-cast", action="store_true", help="only check portraits")
    args = ap.parse_args()

    book = Path(args.book).resolve()
    reader = book / "reader"
    if not args.no_cast:
        run_cast(book, models=args.models, on_progress=lambda m: print(f"  {m}"))
    art = read_json(reader / "characters" / "characters.json")
    reports = {}
    for c in art.get("characters", []):
        rec = {}
        rel = c.get("portrait") or ""
        if rel:
            rec["portrait"] = fix_portrait(reader, rel, rec)
        reports[c["id"]] = rec
        print(f"{c['id']:<10} {rec.get('portrait', '-'):<26} rig={(c.get('rig') or {}).get('kind', 'none')}")
    out = reader / "cast" / "portrait_check.json"
    out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
