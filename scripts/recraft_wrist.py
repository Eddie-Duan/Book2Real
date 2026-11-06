"""Fill the 劳拉 wrist gap in p17 so the hand can wave as one piece.

    python scripts/recraft_wrist.py           # dry run: write the mask + crop only
    python scripts/recraft_wrist.py --go      # one inpaint call ($0.04)

Measured on the page (normalised): the forearm runs from the elbow (~0.60, 0.93)
up to (~0.705, 0.80), and the hand sits from (~0.705, 0.79) outward. The gap is
a short band at the wrist plus the faint hook where the fingers were outlined
over the red paper. The mask is restricted to that band and never touches the
face (face centre is at ~0.52, 0.55).

Output:
    runs/recraft/wrist_mask.png     the mask sent to Recraft
    runs/recraft/wrist_before.jpg   the page as-is
    runs/recraft/wrist_after.jpg    the page with the wrist repaired
    runs/recraft/wrist_cmp.jpg      side-by-side crop for eyeballing
"""
import argparse
import sys

sys.path.insert(0, '.')
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from book2real.vision import recraft

READER = Path("books/charlie_lola/reader")
OUT = Path("runs/recraft")
PAGE = "p17"

# a short band around the wrist, in normalised page coordinates (x0, y0, x1, y1)
BAND = (0.655, 0.755, 0.760, 0.845)
# and the faint finger outline that hangs over the paper just above it
FINGERS = (0.700, 0.775, 0.800, 0.815)

PROMPT = ("pale skin arm with black ink outline, children's picture book style, "
          "solid bright red background, no text")


def build_mask(w: int, h: int) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    for box in (BAND, FINGERS):
        x0, y0, x1, y1 = box
        d.rectangle((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)), fill=255)
    return m.filter(ImageFilter.GaussianBlur(1.0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    art = Image.open(READER / "art" / f"{PAGE}.jpg").convert("RGB")
    w, h = art.size
    mask = build_mask(w, h)
    mask.save(OUT / "wrist_mask.png")
    art.save(OUT / "wrist_before.jpg", quality=93)
    print(f"{PAGE}: {w}x{h}, mask {int(np.asarray(mask).sum() / 255)} px")
    if not args.go:
        print("dry run only; pass --go to spend ~$0.04")
        return 0

    imgs, body = recraft.inpaint(art, mask, PROMPT, n=1)
    after = imgs[0].convert("RGB")
    after.save(OUT / "wrist_after.jpg", quality=93)
    box = (int(0.55 * w), int(0.62 * h), int(0.88 * w), int(0.99 * h))
    a = art.crop(box)
    b = after.crop(box)
    sh = Image.new("RGB", (a.width * 2 + 8, a.height), "white")
    sh.paste(a, (0, 0))
    sh.paste(b, (a.width + 8, 0))
    sh.save(OUT / "wrist_cmp.jpg", quality=92)
    print(f"recraft {body.get('credits')} credits -> wrist_after.jpg, wrist_cmp.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
