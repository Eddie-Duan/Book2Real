"""Layer decomposition: one page image -> 2-3 plate layers with hole fill.

No neural depth model is required. For picture books the useful decomposition
is *figure vs ground*, which we recover from colour distance to the border
colour plus local edge energy, then clean by morphology and connectivity.
That yields coherent regions rather than noisy depth soup, which is what makes
the parallax read as "a pop-up book" and not "a melting picture".
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import imgops as I


@dataclass
class Layer:
    rgb: np.ndarray      # uint8 plate, hole-filled
    alpha: np.ndarray    # float32 0..1
    depth: float         # 0 = far, 1 = near


@dataclass
class PageLayers:
    layers: list          # back -> front
    depth: np.ndarray     # float32 0..1 (debug)
    size: tuple           # (h, w)

    @property
    def count(self) -> int:
        return len(self.layers)


def _ycbcr(a: np.ndarray) -> np.ndarray:
    f = a.astype(np.float32)
    y = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    cb = -0.169 * f[..., 0] - 0.331 * f[..., 1] + 0.5 * f[..., 2]
    cr = 0.5 * f[..., 0] - 0.419 * f[..., 1] - 0.081 * f[..., 2]
    return np.stack([y, cb, cr], axis=-1)


def _norm01(a: np.ndarray, lo_q: float = 2.0, hi_q: float = 98.0) -> np.ndarray:
    lo, hi = np.percentile(a, [lo_q, hi_q])
    if hi - lo < 1e-6:
        return np.zeros(a.shape, np.float32)
    return np.clip((a - lo) / (hi - lo), 0, 1).astype(np.float32)


def decompose(bgr_or_rgb: np.ndarray, *, work: int = 760,
              min_area_frac: float = 0.004, n_layers: int = 3,
              feather: float = 1.8) -> PageLayers:
    """Split a page into far/mid/near plates, each already hole-filled.

    Input array is RGB uint8 (the pipeline converts on read).
    """
    h0, w0 = bgr_or_rgb.shape[:2]
    scale = min(1.0, work / max(h0, w0))
    h, w = int(round(h0 * scale)), int(round(w0 * scale))
    img = I.resize(bgr_or_rgb, w, h) if scale < 1.0 else bgr_or_rgb.copy()

    ycc = _ycbcr(img)
    m = max(4, int(0.05 * min(h, w)))
    border = np.concatenate([
        ycc[:m].reshape(-1, 3), ycc[-m:].reshape(-1, 3),
        ycc[:, :m].reshape(-1, 3), ycc[:, -m:].reshape(-1, 3),
    ])
    bg = np.median(border, axis=0)

    dy = (ycc[..., 0] - bg[0]) / 60.0
    dcb = (ycc[..., 1] - bg[1]) / 22.0
    dcr = (ycc[..., 2] - bg[2]) / 22.0
    dist = np.sqrt(0.55 * dy ** 2 + 0.9 * dcb ** 2 + 0.9 * dcr ** 2)
    dist = _norm01(dist)

    g = I.to_gray(img)
    gy, gx = np.gradient(g)
    grad = np.hypot(gx, gy)
    grad = I.blur_f((_norm01(grad, 50, 99) * 255.0), 3.0) / 255.0

    score = 0.68 * dist + 0.32 * grad
    score = I.blur_f(score * 255.0, max(2.0, min(h, w) / 110.0)) / 255.0
    score = _norm01(score)

    s8 = np.clip(score * 255, 0, 255).astype(np.uint8)
    t = I.otsu(s8)
    near = (s8 > t).astype(np.uint8) * 255
    near = I.morph(near, 3, "open")
    near = I.morph(near, 9, "close")
    near_b = I.components_large(near > 127, min_area_frac)
    near = near_b.astype(np.uint8) * 255

    frac = float((near > 0).mean())
    if frac < 0.012 or frac > 0.90:
        # Nothing figure-like: one plate, plain camera move.
        return PageLayers([Layer(img, np.ones((h, w), np.float32), 0.0)], score, (h, w))

    near_a = I.feather(near, feather)

    mid = np.zeros_like(near)
    if n_layers >= 3:
        t_near = float(s8[near > 0].mean()) if (near > 0).any() else 160.0
        t_mid = max(20.0, t_near * 0.55)
        raw = ((s8 >= t_mid) & (near == 0)).astype(np.uint8) * 255
        if raw.any():
            raw = I.morph(raw, 3, "open")
            raw = I.morph(raw, 7, "close")
            keep = I.components_large(raw > 127, min_area_frac * 0.6)
            mid = keep.astype(np.uint8) * 255

    if mid.any() and float((mid > 0).mean()) > 0.004:
        mid_a = (I.feather(mid, feather * 1.2) * (1.0 - near_a)).astype(np.float32)
        far_a = np.clip(1.0 - (near_a + mid_a), 0, 1).astype(np.float32)
        far = I.inpaint(img, (near > 0) | (mid > 0))
        mid_img = I.inpaint(img, near > 0)
        return PageLayers(
            [Layer(far, far_a, 0.0), Layer(mid_img, mid_a, 0.5), Layer(img, near_a, 1.0)],
            score, (h, w))

    far_a = np.clip(1.0 - near_a, 0, 1).astype(np.float32)
    far = I.inpaint(img, near > 0)
    return PageLayers([Layer(far, far_a, 0.0), Layer(img, near_a, 1.0)], score, (h, w))
