#!/usr/bin/env python3
"""OCR bbox -> stroke-level text mask.

Input JSON: a list of blocks, or {"text_blocks": [...]}.
Each block needs a 0-1000 normalised bbox in one of these keys:
    bbox_norm / bbox / box / bbox_2d   as [ymin, xmin, ymax, xmax]

What it does per box:
  1. estimates the local background (median filter)
  2. keeps pixels that deviate from it (ink), plus luminance outliers
  3. falls back to the full box when no ink is detected
  4. dilates the union by --dilate px (adaptive: 0.4% of the short side)

Dependencies: numpy, Pillow only.

Usage:
  python make_text_mask.py --image page.png --blocks ocr.json \
      --out text_mask.png --dilate 6 --debug-overlay mask_check.png
"""

import argparse
import json
import sys

import numpy as np
from PIL import Image, ImageFilter


def load_boxes(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("text_blocks") or data.get("blocks") or data.get("data") or []
    boxes = []
    for b in data:
        if not isinstance(b, dict):
            continue
        raw = b.get("bbox_norm") or b.get("bbox") or b.get("box") or b.get("bbox_2d")
        if raw and len(raw) == 4:
            boxes.append([float(v) for v in raw])
    return boxes


def ink_mask(gray, x0, y0, x1, y1, thr):
    """Boolean ink mask for one box, in absolute image coordinates."""
    crop = gray[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    bg = np.asarray(
        Image.fromarray(crop).filter(ImageFilter.MedianFilter(size=9))
    ).astype(np.int16)
    diff = np.abs(crop.astype(np.int16) - bg)
    m = diff > thr
    # luminance outliers catch low-saturation but high-contrast strokes
    med = int(np.median(crop))
    m |= np.abs(crop.astype(np.int16) - med) > 45
    if m.mean() < 0.01:  # nothing detected -> keep the whole box
        m = np.ones_like(m, dtype=bool)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--blocks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dilate", type=int, default=-1,
                    help="dilate px; -1 = adaptive 0.4%% of the short side")
    ap.add_argument("--thr", type=int, default=18, help="ink threshold on 0-255 luminance")
    ap.add_argument("--debug-overlay", default=None)
    a = ap.parse_args()

    im = Image.open(a.image).convert("RGB")
    W, H = im.size
    gray = np.asarray(im.convert("L"))
    mask = np.zeros((H, W), dtype=bool)

    boxes = load_boxes(a.blocks)
    if not boxes:
        sys.exit(f"no bbox found in {a.blocks}")

    for ymin, xmin, ymax, xmax in boxes:
        x0 = max(0, int(xmin / 1000 * W))
        x1 = min(W, int(np.ceil(xmax / 1000 * W)))
        y0 = max(0, int(ymin / 1000 * H))
        y1 = min(H, int(np.ceil(ymax / 1000 * H)))
        m = ink_mask(gray, x0, y0, x1, y1, a.thr)
        if m is not None:
            mask[y0:y1, x0:x1] |= m

    dilate = a.dilate
    if dilate < 0:
        dilate = max(2, int(round(min(W, H) * 0.004)))

    mimg = Image.fromarray((mask * 255).astype(np.uint8))
    if dilate > 0:
        mimg = mimg.filter(ImageFilter.MaxFilter(size=2 * dilate + 1))
    mimg.save(a.out)

    cov = float((np.asarray(mimg) > 127).mean() * 100)
    print(f"mask saved: {a.out}  coverage={cov:.2f}%  dilate={dilate}px  boxes={len(boxes)}")

    if a.debug_overlay:
        red = Image.new("RGB", im.size, (255, 0, 0))
        alpha = mimg.point(lambda v: 110 if v > 127 else 0)
        Image.composite(red, im, alpha).save(a.debug_overlay)
        print(f"overlay saved: {a.debug_overlay}")


if __name__ == "__main__":
    main()
