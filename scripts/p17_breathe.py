"""Move a piece by *translation* when it has no closed outline.

The p17 hand overlaps the cheek, so there is no boundary to cut along and every
geometry attempt merges the arm into the page (measured; see docs/RECRAFT.md).
What still works, and what a picture book actually does, is to shift the whole
frame a little: the character's arm rises because the arm and everything around
it move together, and there is no seam to hide because nothing is cut.

    python scripts/p17_breathe.py

Renders a small, slow shift + breathing loop from the repaired page. The motion
is documented (amplitudes in the plan below), subtle on purpose, and needs no
mask at all.
"""
import sys

sys.path.insert(0, '.')
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path("runs/agent/p17")
SRC = OUT / "frame_fixed.jpg"          # wrist repaired, hand left on the cheek
OUT_MOV = OUT / "p17_breathe.gif"

FPX = 20          # fps
SECONDS = 2.8
SHIFT_X = 3.0     # px, horizontal breathing
SHIFT_Y = 2.0     # px
ZOOM = 0.006      # 0.6%


def main() -> int:
    base = Image.open(SRC).convert("RGB")
    w, h = base.size
    n = int(FPX * SECONDS)
    frames = []
    for i in range(n):
        t = i / (n - 1)
        phase = np.sin(2 * np.pi * t)
        dx = SHIFT_X * phase
        dy = SHIFT_Y * np.sin(2 * np.pi * t + 0.8)
        s = 1.0 + ZOOM * (0.5 - 0.5 * np.cos(2 * np.pi * t))
        nw, nh = int(w * s), int(h * s)
        frame = base.resize((nw, nh), Image.BICUBIC)
        left = int((nw - w) / 2 + dx)
        top = int((nh - h) / 2 + dy)
        frames.append(frame.crop((left, top, left + w, top + h)))
    small = [f.resize((int(w * 0.62), int(h * 0.62)), Image.LANCZOS) for f in frames]
    small[0].save(OUT_MOV, save_all=True, append_images=small[1:],
                  duration=max(20, int(1000 / FPX)), loop=0, optimize=True)
    print(f"{OUT_MOV}  frames={n}  size={small[0].size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
