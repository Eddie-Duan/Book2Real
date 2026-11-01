"""Shot quality control: decide whether a rendered shot is usable.

Everything here works on the *encoded* clip, so it also catches encode-level
mistakes (wrong duration, black frames, dropped frames) rather than trusting
what the renderer meant to produce.

Calibration notes (2026-10-31, real scanned books):
  * `still` must not treat a slow camera move as a frozen frame: consecutive
    deltas of a 7 s dolly are ~4e-4 while the shot is clearly moving. We only
    count runs of >= 3 near-identical frames as "still".
  * `sharpness` is measured on the page card (or the full frame in `fill`
    mode), against the page resized to the *same* box. Comparing a 720p frame
    with a 2.5k page was the old mistake.
  * `drift_ncc` is kept as a diagnostic but no longer gates acceptance: a
    camera push makes the first and last frame legitimately different. Color
    fidelity (histogram intersection against the page) is the invariant that
    actually catches a corrupted plate.
"""
from __future__ import annotations

import numpy as np

from . import imgops as I
from .config import QCConfig
from .contracts import QCVerdict, ShotSpec
from .encode import grab_frames

SMALL = (192, 108)
HIST_BINS = 8


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float32) - a.mean()
    b = b.astype(np.float32) - b.mean()
    den = float(np.linalg.norm(a) * np.linalg.norm(b))
    if den < 1e-6:
        return 0.0
    return float((a * b).sum() / den)


def _gray(rgb: np.ndarray) -> np.ndarray:
    f = rgb.astype(np.float32)
    return 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]


def _hist(rgb: np.ndarray) -> np.ndarray:
    q = (rgb.astype(np.uint16) * HIST_BINS // 256).clip(0, HIST_BINS - 1)
    idx = (q[..., 0] * HIST_BINS + q[..., 1]) * HIST_BINS + q[..., 2]
    h = np.bincount(idx.ravel(), minlength=HIST_BINS ** 3).astype(np.float32)
    return h / max(1.0, float(h.sum()))


def _hist_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.minimum(_hist(a), _hist(b)).sum())


def _still_ratio(deltas: np.ndarray, eps: float = 1e-5, run: int = 2) -> float:
    """Fraction of frame pairs that belong to a run of >= `run` frozen pairs."""
    near = deltas < eps
    frozen = 0
    i = 0
    n = len(near)
    while i < n:
        if not near[i]:
            i += 1
            continue
        j = i
        while j < n and near[j]:
            j += 1
        if j - i >= run:
            frozen += j - i
        i = j
    return frozen / max(1, n)


def _box_px(box, size):
    """(x, y, w, h) normalised 0..1 -> integer pixels, clamped to `size`."""
    w, h = size
    x = int(round(box[0] * w))
    y = int(round(box[1] * h))
    bw = max(2, int(round(box[2] * w)))
    bh = max(2, int(round(box[3] * h)))
    x = max(0, min(x, w - bw))
    y = max(0, min(y, h - bh))
    return x, y, bw, bh


def _inner(box, margin=0.08):
    x, y, w, h = box
    dx, dy = int(round(w * margin)), int(round(h * margin))
    return x + dx, y + dy, max(2, w - 2 * dx), max(2, h - 2 * dy)


def evaluate(clip_path, spec: ShotSpec, page_rgb: np.ndarray, cfg: QCConfig,
             *, card_box: tuple | None = None, fill: bool = False) -> QCVerdict:
    frames = grab_frames(clip_path, SMALL, gray=False, max_frames=max(8, spec.frames + 2))
    metrics: dict = {}
    reasons: list[str] = []

    n = len(frames)
    metrics["frames"] = n
    if n < 2:
        return QCVerdict(False, 0.0, ["decode produced <2 frames"], metrics)

    rgb = np.stack([x.astype(np.uint8) for x in frames])
    f = _gray(rgb)
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2)) / 255.0
    motion = float(np.abs(f - f[0]).mean(axis=(1, 2)).mean() / 255.0)
    still = _still_ratio(d)
    flicker = float(d.mean())
    means = f.mean(axis=(1, 2))
    black = float((means < 6).mean())
    bright = float(abs(means[-1] - means[0]) / 255.0)

    # Page reference on the same grid as the measured region.
    sw, sh = SMALL
    if fill or card_box is None:
        box = (0, 0, sw, sh)
        page_fit = I.cover(page_rgb, sw, sh)
    else:
        box = _box_px(card_box, SMALL)
        bx, by, bw, bh = box
        page_fit = I.resize(page_rgb, bw, bh)
    ix, iy, iw, ih = _inner(box)
    page_ref = page_fit[iy - box[1]:iy - box[1] + ih, ix - box[0]:ix - box[0] + iw]

    page_g = (I.to_gray(page_ref) * 255).astype(np.uint8)
    ref_sharp = max(1e-3, I.laplacian_var(page_g))
    sharp = float(np.mean([
        I.laplacian_var((_gray(rgb[i, iy:iy + ih, ix:ix + iw])).astype(np.uint8))
        for i in range(n)
    ]) / ref_sharp)

    mid = n // 2
    color_sim = _hist_sim(rgb[mid, iy:iy + ih, ix:ix + iw], page_ref)
    c0 = _gray(rgb[0, iy:iy + ih, ix:ix + iw])
    c1 = _gray(rgb[-1, iy:iy + ih, ix:ix + iw])
    drift = _ncc(c0, c1)

    metrics.update({
        "motion": round(motion, 5), "flicker": round(flicker, 5),
        "still_ratio": round(still, 3), "black_ratio": round(black, 3),
        "brightness_drift": round(bright, 4), "sharpness_ratio": round(sharp, 3),
        "color_sim": round(color_sim, 3), "drift_ncc": round(drift, 4),
        "duration": round(spec.frames / max(1, spec.fps), 3),
        "region": "fill" if fill else "card",
    })

    if black > 0.05:
        reasons.append("black frames")
    if flicker > cfg.max_flicker:
        reasons.append(f"flicker {flicker:.3f} > {cfg.max_flicker}")
    if still > cfg.max_still_ratio:
        reasons.append(f"still {still:.2f} > {cfg.max_still_ratio}")
    if motion < cfg.min_motion:
        reasons.append(f"motion {motion:.4f} < {cfg.min_motion}")
    if sharp < cfg.min_sharpness_ratio:
        reasons.append(f"sharpness {sharp:.2f} < {cfg.min_sharpness_ratio}")
    if color_sim < cfg.min_color_sim:
        reasons.append(f"color {color_sim:.2f} < {cfg.min_color_sim}")

    score = float(np.clip(
        0.32 * min(1.0, motion / 0.02)
        + 0.22 * max(0.0, 1.0 - flicker / max(cfg.max_flicker, 1e-6))
        + 0.26 * color_sim
        + 0.20 * min(1.0, sharp),
        0.0, 1.0))
    metrics["score"] = round(score, 3)
    return QCVerdict(not reasons, round(score, 3), reasons, metrics)
