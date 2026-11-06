"""Ink as a wall has a failure mode: black hair *is* ink.

p05's Lotta has black curly hair. The ink-wall operator removes it, so the cut
keeps the dress and loses the head. The fix is not a lower threshold — that
reopens the face/arm merge on p17 — it is to classify *areas* as ink-walls vs
ink-shapes:

* a wall is thin (small area, high perimeter/area) and long;
* a shape (hair, an eye) is thick — its distance transform peaks well above the
  line width.

    python -m book2real agent walls --from page.jpg      # classify dark areas
    python -m book2real agent part  --from page.jpg --at ... --hair
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def dark_areas(rgb: np.ndarray, *, luma_rel: float = 0.72, absolute_max: int = 150,
               open_px: int = 0) -> np.ndarray:
    grey = rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    blur = np.asarray(Image.fromarray(np.clip(grey, 0, 255).astype(np.uint8), "L")
                      .filter(ImageFilter.GaussianBlur(9)), np.float32)
    dark = (grey < blur * luma_rel) & (grey < absolute_max)
    return dark


def classify(rgb: np.ndarray, *, line_width: float = 3.0) -> dict:
    """Split dark pixels into `walls` (thin lines) and `shapes` (thick blobs).

    Measured with a distance transform: a pixel whose distance to the nearest
    non-dark pixel exceeds `line_width` belongs to a thick shape, not a line.
    """
    dark = dark_areas(rgb)
    if not dark.any():
        return {"walls": np.zeros_like(dark), "shapes": np.zeros_like(dark)}
    try:
        import cv2

        dist = cv2.distanceTransform(dark.astype(np.uint8), cv2.DIST_L2, 5)
    except Exception:                                  # noqa: BLE001
        dist = np.zeros(dark.shape, np.float32)
    thick = dist > line_width
    # grow the thick seed back to the whole dark blob it sits in
    shapes = np.zeros_like(dark)
    try:
        import cv2

        n, lab = cv2.connectedComponents(dark.astype(np.uint8), connectivity=8)
        for i in range(1, n):
            blob = lab == i
            if (blob & thick).any():
                shapes |= blob
    except Exception:                                  # noqa: BLE001
        shapes = thick
    walls = dark & ~shapes
    return {"walls": walls, "shapes": shapes,
            "dark_share": round(float(dark.mean()), 4),
            "shape_share": round(float(shapes.mean()), 4)}


def grow(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask
    k = 2 * r + 1
    return np.asarray(Image.fromarray((mask.astype(np.uint8) * 255), "L")
                      .filter(ImageFilter.MaxFilter(k))) > 127
