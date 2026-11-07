"""Honour `keep_text` and `restore` in curation when erasing printed text.

Two page-level escape hatches, both optional:

    "keep_text": ["升级版"]      # do not erase these OCR blocks at all
    "restore": [{"box":[...],    # cover exactly this area with flat paper colour
                 "fill": "auto"}]

`restore` exists for a printed label that sits on a drawing we want to keep (the
cover's 升级版 badge): erasing the grey glyphs with an inpainter smears the
drawing, while filling the tile with its surrounding colour leaves a clean badge
face. Both are curation data, so the behaviour is reviewable per page.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw


def drop_kept(blocks: list[dict], keep: list[str] | None) -> list[dict]:
    """Remove OCR blocks whose text matches a `keep_text` entry."""
    if not keep:
        return blocks
    keep = [k.strip() for k in keep if k and k.strip()]
    out = []
    for b in blocks:
        text = (b.get("text") or "").strip()
        if any(k and k in text for k in keep):
            continue
        out.append(b)
    return out


def apply_restore(rgb: np.ndarray, boxes: list[dict] | None) -> np.ndarray:
    """Fill each restore box with the median colour just outside it."""
    if not boxes:
        return rgb
    out = rgb.copy()
    h, w = rgb.shape[:2]
    for item in boxes:
        box = item.get("box")
        if not box:
            continue
        x0, y0, x1, y1 = (float(v) for v in box)
        ix0, iy0 = int(x0 * w), int(y0 * h)
        ix1, iy1 = int(x1 * w), int(y1 * h)
        if ix1 - ix0 < 2 or iy1 - iy0 < 2:
            continue
        pad = 6
        ring = np.concatenate([
            rgb[max(0, iy0 - pad):iy0, ix0:ix1].reshape(-1, 3),
            rgb[iy1:min(h, iy1 + pad), ix0:ix1].reshape(-1, 3),
            rgb[iy0:iy1, max(0, ix0 - pad):ix0].reshape(-1, 3),
            rgb[iy0:iy1, ix1:min(w, ix1 + pad)].reshape(-1, 3),
        ]).astype(np.float32)
        colour = (np.median(ring, axis=0) if ring.size else np.array([255, 255, 255]))
        fill = str(item.get("fill") or "auto")
        if fill != "auto":
            try:
                colour = np.array([int(v) for v in fill.split(",")], np.float32)
            except Exception:                          # noqa: BLE001
                pass
        tile = Image.fromarray(np.zeros((iy1 - iy0, ix1 - ix0, 3), np.uint8))
        ImageDraw.Draw(tile).rectangle((0, 0, tile.width, tile.height),
                                       fill=tuple(int(v) for v in colour))
        out[iy0:iy1, ix0:ix1] = np.asarray(tile)
    return out
