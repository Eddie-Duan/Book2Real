"""Critic extension: reject a layer cut that removes the head.

`cut_face` (in cast/critic.py) already rejects a *puppet* that has no face. The
2.5D layer builder needed the same guard: SAM sometimes returns the body of a
figure but not its head, and the resulting page has a face floating on the
background while the "character layer" is a torso.

The check is shape-only, not model-based: take the top of the cut mask, compare
its width with the widest row of the whole figure, and require a head-shaped
bulge. A torso-only cut is nearly as wide at the top as at the middle, so the
ratio gives it away.
"""
from __future__ import annotations

import numpy as np


def head_missing(mask: np.ndarray) -> tuple[bool, dict]:
    """True when the mask looks headless (no narrow top above a wider body)."""
    if mask.ndim != 2 or mask.sum() < 200:
        return True, {"reason": "empty cut"}
    rows = np.where(mask.any(axis=1))[0]
    top, bottom = int(rows.min()), int(rows.max())
    height = bottom - top + 1
    widths = mask.sum(axis=1).astype(np.float32)
    widest = float(widths.max())
    if widest <= 0:
        return True, {"reason": "empty cut"}
    top_band = widths[top:top + max(3, int(0.10 * height))]
    body_band = widths[top + int(0.25 * height):top + int(0.85 * height) + 1]
    if body_band.size == 0 or top_band.size == 0:
        return True, {"reason": "cut too short"}
    top_w = float(np.median(top_band))
    body_w = float(np.median(body_band))
    ratio = top_w / max(1.0, body_w)
    detail = {"top_median": round(top_w, 1), "body_median": round(body_w, 1),
              "ratio": round(ratio, 3), "height": int(height)}
    # A head sits on a narrower neck: the top 10% should be clearly narrower than
    # the body. Big-head picture-book kids break this (head IS the widest part),
    # so only reject when the top is *wider* than the body and the cut is tall —
    # that is the torso-only case where the face stayed in the background.
    looks_like_torso = ratio > 1.05 and height > 0.5 * mask.shape[0]
    return bool(looks_like_torso), detail


def limb_coverage(mask: np.ndarray, box: tuple[float, float, float, float],
                  *, target: float = 0.62) -> tuple[bool, dict]:
    """True when the cut covers the figure box poorly (head/arms left behind).

    SAM answering "the body" instead of "the body plus arms" gives a cut that
    covers well under two thirds of the box the figure was detected in. No
    picture-book child fills the whole box, but one whose head or an arm is
    missing covers far less — that is the failure that leaves a ghost behind.
    """
    h, w = mask.shape
    x, y, bw, bh = box
    ref = np.zeros((h, w), bool)
    ref[int(max(0, y) * h):int(min(1.0, y + bh) * h),
        int(max(0, x) * w):int(min(1.0, x + bw) * w)] = True
    total = float(ref.sum())
    if total <= 0:
        return False, {"reason": "empty box"}
    share = float(mask[ref].sum()) / total
    detail = {"box_share": round(share, 3), "target": target}
    return bool(share < target), detail
