#!/usr/bin/env python3
"""Composite an edited image back into the original, inside the mask only.

This is the pixel lock. Outside the mask the result is bit-identical to the
original; the feather ramps inward (never outward), so no half-transparent
stroke halo survives at the text edges.

Dependencies: numpy, Pillow only.

Usage:
  python composite.py --original work/p_00_normalized.png \
      --edited work/p_03_inpainted.png --mask work/p_03_text_mask.png \
      --out work/p_03_clean_base.png --feather 3
"""

import argparse

import numpy as np
from PIL import Image, ImageFilter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", required=True)
    ap.add_argument("--edited", required=True)
    ap.add_argument("--mask", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--feather", type=int, default=3,
                    help="inward feather px; 0 = hard edge")
    a = ap.parse_args()

    orig = Image.open(a.original).convert("RGB")
    edit = Image.open(a.edited).convert("RGB")
    if edit.size != orig.size:
        raise SystemExit(
            f"size mismatch: edited {edit.size} != original {orig.size}. "
            f"Resize the edited output back to the original size first; "
            f"skipping this step makes the composite miss the text areas."
        )

    m = Image.open(a.mask).convert("L")
    if m.size != orig.size:
        raise SystemExit(f"mask size {m.size} != image size {orig.size}")

    hard = np.asarray(m) > 127
    if a.feather > 0:
        soft = np.asarray(m.filter(ImageFilter.GaussianBlur(a.feather))).astype(np.float32) / 255.0
    else:
        soft = hard.astype(np.float32)

    # feather only inside: outside the hard mask alpha is forced to 0
    alpha = (soft * hard.astype(np.float32))[..., None]

    o = np.asarray(orig).astype(np.float32)
    e = np.asarray(edit).astype(np.float32)
    out = o * (1.0 - alpha) + e * alpha
    Image.fromarray(np.clip(np.round(out), 0, 255).astype(np.uint8)).save(a.out)

    changed = float((np.abs(np.asarray(orig).astype(np.int16) -
                            np.asarray(Image.open(a.out)).astype(np.int16))
                     .max(axis=2) > 0).mean() * 100)
    print(f"saved {a.out}; mask covers {hard.mean()*100:.2f}% of pixels, "
          f"{changed:.2f}% actually changed")


if __name__ == "__main__":
    main()
