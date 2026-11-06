"""Cut a piece by its *negative space*, not by its own outline.

p17 is the hard case: the hand is drawn overlapping the cheek, so there is no
closed ink outline around the arm — the arm is bounded by the paper on one side
and by the face on the other. No single-outline operator can separate it; but
the *region that is not face, not dress, not hair* under a few seed points is
exactly the arm.

So: paint the big neighbouring shapes (from the cell table) as walls, add the ink
line, and take the connected component under the seeds. That component follows
the true arm boundary on every side, because every boundary is either ink or one
of the painted shapes.

The agent's input is the same either way: a few points on the piece plus the ids
of the shapes to exclude. Both are readings of the `probe` table, not magic.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from . import cells as C


def build_excluding(art: np.ndarray, points: list[tuple[float, float]],
                    exclude_cells: list[int], *, close: int = 1,
                    pad: int = 1) -> tuple[np.ndarray, dict]:
    h, w = art.shape[:2]
    cs = C.cells(art, close=close)
    walls = C.ink_mask(art)
    for i in exclude_cells:
        if 0 <= i < len(cs):
            walls |= cs[i]
    walls = np.asarray(Image.fromarray((walls.astype(np.uint8) * 255), "L")
                       .filter(ImageFilter.MaxFilter(1 + 2 * close))) > 127
    free = (~walls).astype(np.uint8)
    try:
        import cv2

        n, lab = cv2.connectedComponents(free, connectivity=4)
    except Exception:                                  # noqa: BLE001
        lab = free.astype(np.int32)
        n = int(lab.max()) + 1
    hits = []
    for x, y in points:
        px, py = int(x * w), int(y * h)
        hits.append(int(lab[min(h - 1, py), min(w - 1, px)]))
    hits = [h_ for h_ in hits if h_ > 0]
    if not hits:
        raise ValueError("no free region under the seeds (they landed on a wall)")
    mask = np.zeros((h, w), bool)
    for h_ in hits:
        mask |= lab == h_
    grow = np.asarray(Image.fromarray((mask.astype(np.uint8) * 255), "L")
                      .filter(ImageFilter.MaxFilter(1 + 2 * pad))) > 127
    alpha = grow.astype(np.uint8) * 255
    ys, xs = np.where(alpha > 0)
    m = 3
    x0, y0 = max(0, int(xs.min()) - m), max(0, int(ys.min()) - m)
    x1, y1 = min(w, int(xs.max()) + 1 + m), min(h, int(ys.max()) + 1 + m)
    rgba = np.dstack([art[y0:y1, x0:x1], alpha[y0:y1, x0:x1]])
    meta = {"offset_px": [x0, y0], "size_px": [int(x1 - x0), int(y1 - y0)],
            "excluded_cells": exclude_cells, "how": "negative-space"}
    return rgba, meta


def biggest_cells(art: np.ndarray, *, close: int = 1, limit: int = 6) -> list[int]:
    """The neighbouring shapes an agent would name as "not the arm"."""
    cs = C.cells(art, close=close)
    order = sorted(range(len(cs)), key=lambda i: -int(cs[i].sum()))
    return order[:limit]
