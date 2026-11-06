"""p17: let Recraft redraw the hand *off* the cheek, so the arm becomes a clean shape.

Diagnosis first (measured, not guessed):

* `python -m book2real agent probe` shows the arm shares a colour region with the
  face and the paper — the ink does not close around the hand because the hand is
  drawn overlapping the cheek;
* every geometric attempt (colour cells, ink walls, negative space, rect cut)
  either merges the arm into the page or carries part of the cheek along.

The honest fix is not a cleverer mask: it is to change the drawing so the arm has
a closed outline. Recraft inpaints the small area *between the hand and the
cheek* (paper colour), turning the wrist into an edge we can cut along.

    python scripts/p17_separate_hand.py            # dry run, writes the mask
    python scripts/p17_separate_hand.py --go       # one inpaint ($0.04)
"""
import argparse
import sys

sys.path.insert(0, '.')
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from book2real.vision import recraft

OUT = Path("runs/agent/p17")
SRC = OUT / "frame_fixed.jpg"
# the band between the outer edge of the hand and the cheek: paper, not skin
BAND = (0.690, 0.615, 0.905, 0.700)
PROMPT = ("solid bright red background, flat children's picture book illustration, "
          "clean thin black ink outline, no text")


def mask(w: int, h: int) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    x0, y0, x1, y1 = BAND
    ImageDraw.Draw(m).rectangle((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)), fill=255)
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    args = ap.parse_args()
    art = Image.open(SRC).convert("RGB")
    m = mask(*art.size)
    m.save(OUT / "hand_mask.png")
    print(f"mask {int(np.asarray(m).sum() / 255)} px over {BAND}")
    if not args.go:
        print("dry run; pass --go to spend ~$0.04")
        return 0
    imgs, body = recraft.inpaint(art, m, PROMPT, n=1)
    out = imgs[0].convert("RGB")
    out.save(OUT / "frame_hand_sep.jpg", quality=93)
    w, h = art.size
    box = (int(0.55 * w), int(0.55 * h), int(0.95 * w), int(0.95 * h))
    a, b = art.crop(box), out.crop(box)
    sh = Image.new("RGB", (a.width * 2 + 8, a.height), "white")
    sh.paste(a, (0, 0))
    sh.paste(b, (a.width + 8, 0))
    sh.save(OUT / "hand_cmp.jpg", quality=92)
    print(f"recraft {body.get('credits')} credits -> frame_hand_sep.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
