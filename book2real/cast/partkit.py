"""Point-prompted cut that respects the ink line.

    python -m book2real agent part --from frame.jpg --at 0.78,0.70 --at 0.73,0.85 \\
        --to out --name arm_r --pivot-from mask

For each `--at` point it finds the *cell* (page minus ink line) under the point,
merges neighbouring cells of the same colour, and unions the seeds. The alpha is
that boundary — an ink outline, not a rectangle — so no cheek or dress comes
along unless the agent asked for it.

Then it derives the pivot from the part's own geometry: the end of the piece
farthest from the coloured tip (the hand) — that is the shoulder. No hand-tuned
numbers, which is what makes this reproducible by a different agent.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from . import cells as C


def build(art: np.ndarray, points: list[tuple[float, float]], *, tol: float = 26.0,
          close: int = 1, pad: int = 1, keep_thick: bool = False) -> tuple[np.ndarray, dict]:
    """Cut the cells under `points`; `keep_thick` keeps black hair as content."""
    h, w = art.shape[:2]
    cs = C.cells(art, close=close, keep_thick=keep_thick)
    chosen: set[int] = set()
    seeds = []
    # a seed names ONE cell. Merging same-coloured neighbours here was wrong: it
    # joins the arm to the paint on the dress, or to the paper, because they are
    # the same colour. Merging is a decision, so it is left to the spec (`cut`).
    for x, y in points:
        seed = C.region_of(cs, (int(x * w), int(y * h)))
        seeds.append(seed)
        if seed >= 0:
            chosen.add(seed)
    mask = np.zeros((h, w), bool)
    for i in chosen:
        mask |= cs[i]
    if not mask.any():
        raise ValueError("no cell under the given points")
    grow = np.asarray(Image.fromarray((mask.astype(np.uint8) * 255), "L")
                      .filter(ImageFilter.MaxFilter(1 + 2 * pad))) > 127
    alpha = grow.astype(np.uint8) * 255
    ys, xs = np.where(alpha > 0)
    m = 3
    x0, y0 = max(0, int(xs.min()) - m), max(0, int(ys.min()) - m)
    x1, y1 = min(w, int(xs.max()) + 1 + m), min(h, int(ys.max()) + 1 + m)
    rgba = np.dstack([art[y0:y1, x0:x1], alpha[y0:y1, x0:x1]])
    meta = {"offset_px": [x0, y0], "size_px": [int(x1 - x0), int(y1 - y0)],
            "cells": len(chosen), "seeds": seeds, "how": "cells+ink"}
    return rgba, meta


def end_pivot(rgba: np.ndarray) -> list[float]:
    """Pivot at the far end of the piece from its widest (hand) part.

    For a limb the hand is the widest blob; the shoulder is the end of the
    skeleton furthest from it. Approximated by: take the extreme point of the
    alpha along the principal axis that is farther from the widest row.
    """
    a = rgba[..., 3] > 127
    if not a.any():
        return [0.5, 0.5]
    h, w = a.shape
    rows = a.sum(axis=1).astype(np.float32)
    widest_row = int(np.argmax(rows))
    ys, xs = np.where(a)
    # the far end = the pixel whose row is farthest from the widest row
    far = np.abs(ys - widest_row)
    idx = int(np.argmax(far))
    px = float(xs[idx]) / max(1, w - 1)
    py = float(ys[idx]) / max(1, h - 1)
    return [round(px, 3), round(py, 3)]


def preview(rgba: np.ndarray, bg: tuple[int, int, int] = (207, 63, 47)) -> Image.Image:
    canvas = Image.new("RGB", rgba.shape[1:] , bg)
    canvas.paste(Image.fromarray(rgba, "RGBA"), (0, 0), Image.fromarray(rgba, "RGBA"))
    return canvas
