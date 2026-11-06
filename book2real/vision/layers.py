"""Layered 2.5D: one flat illustration -> background + characters + depth map.

The reader shows a still page. A *little* documented motion is what makes a
picture book feel alive, but the earlier "shake the whole page" version read as
cheap. This module produces the data for the opposite look: real occlusion
layers that move by different amounts, so the paper behaves like a diorama.

    art/<page>.jpg      the cleaned page (already has ink-thinning applied)
    layers/<page>.json  {"depth": "layers/p03_depth.png", "layers": [...]}

The background is repaired by an annular (ring) inpainting: for every figure
pixel the nearest paper pixel just outside the figure is copied in, which keeps
the wallpaper pattern, the wood grain and the paper lighting because they are
sampled from 1-3 px away — not from the page border.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ..util import ensure_dir, imread, write_json
from . import depth as depth_mod
from . import inpaint as inpaint_mod

FAR_SHIFT = 0.006      # background travel, fraction of page width
NEAR_SHIFT = 0.022     # character travel
FAR_ZOOM = 1.05        # background is scaled up so a shift never shows an edge


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask
    k = max(3, (2 * r + 1) | 1)
    return np.asarray(Image.fromarray((mask.astype(np.uint8) * 255), "L")
                      .filter(ImageFilter.MaxFilter(k))) > 127


def _erode(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask
    k = max(3, (2 * r + 1) | 1)
    return np.asarray(Image.fromarray((mask.astype(np.uint8) * 255), "L")
                      .filter(ImageFilter.MinFilter(k))) > 127


def _open(mask: np.ndarray, r: int) -> np.ndarray:
    return _dilate(_erode(mask, r), r)


def _feather(mask: np.ndarray, r: float) -> np.ndarray:
    im = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    return np.asarray(im.filter(ImageFilter.GaussianBlur(max(0.5, r))), np.float32) / 255.0


def ring_fill(rgb: np.ndarray, hole: np.ndarray, rings: int = 6) -> np.ndarray:
    """Grow the paper inward one pixel ring at a time (numpy only).

    Each pass replaces every unknown pixel that has a known 4-neighbour with the
    mean of those known neighbours, so pattern edges advance continuously.

    The propagation must stop at the hole's own edge: `ring_fill` bails out of
    the shift at the page border, but a hole is not a wrap-around region, so the
    shift has to be a plain "no neighbour" (not np.roll, which would bring the
    opposite page edge in as the seed — that is how a slab of the sky blue
    border used to get copied into the figure's place).
    """
    out = rgb.astype(np.float32).copy()
    known = ~hole
    if not known.any():
        return rgb
    h, w = known.shape
    # the hole's own border is the only legal seed: a neighbour across the hole
    # edge is *inside* the hole, and outside the hole at the page border there is
    # nothing (the old np.roll version seeded the fill from the far page edge,
    # which is how a slab of sky blue appeared where the figures were)
    for _ in range(rings):
        unknown = hole & ~known
        if not unknown.any():
            break
        hh, ww = unknown.shape
        acc = np.zeros_like(out)
        cnt = np.zeros(unknown.shape, np.float32)
        # right neighbour (x+1) -> this pixel
        m = unknown[:, :ww - 1] & known[:, 1:]
        acc[:, :ww - 1][m] += out[:, 1:][m]
        cnt[:, :ww - 1][m] += 1
        # left neighbour (x-1)
        m = unknown[:, 1:] & known[:, :ww - 1]
        acc[:, 1:][m] += out[:, :ww - 1][m]
        cnt[:, 1:][m] += 1
        # down neighbour (y+1)
        m = unknown[:hh - 1, :] & known[1:, :]
        acc[:hh - 1, :][m] += out[1:, :][m]
        cnt[:hh - 1, :][m] += 1
        # up neighbour (y-1)
        m = unknown[1:, :] & known[:hh - 1, :]
        acc[1:, :][m] += out[:hh - 1, :][m]
        cnt[1:, :][m] += 1
        new = cnt > 0
        if not new.any():
            break
        out[new] = acc[new] / cnt[new][:, None]
        known[new] = True
    if not known.all():                       # fallback for huge holes
        med = np.median(out[~hole].reshape(-1, 3), axis=0)
        out[~known] = med
    return np.clip(out, 0, 255).astype(np.uint8)


def _ring_fill_debug(rgb, hole, rings=6):
    """Same as ring_fill but returns (image, first_ring_source_colours)."""
    return ring_fill(rgb, hole, rings)


def scan_fill(rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Fill each hole row by carrying colour in from the nearest paper on both sides.

    A ring grow is hopeless for a wide hole (hundreds of passes, one flat slab).
    A scanline fill is what a retoucher does behind a figure: for every row take
    the nearest known pixel on the left and on the right and interpolate the gap;
    then the same along columns for pixels that had paper on one side only.
    """
    out = rgb.astype(np.float32).copy()
    h, w = rgb.shape[:2]
    filled = np.zeros_like(hole)
    for y in range(h):
        row_hole = np.where(hole[y])[0]
        if row_hole.size == 0:
            continue
        xs = np.where(~hole[y])[0]
        if xs.size < 2:
            continue
        for run in _runs(row_hole):
            lo, hi = run
            left = xs[xs < lo]
            right = xs[xs > hi]
            if left.size == 0 or right.size == 0:
                continue
            x0, x1 = int(left[-1]), int(right[0])
            for c in range(3):
                out[y, lo:hi + 1, c] = np.interp(np.arange(lo, hi + 1),
                                                 [x0, x1], [out[y, x0, c], out[y, x1, c]])
            filled[y, lo:hi + 1] = True
    for x in range(w):
        col = np.where(hole[:, x] & ~filled[:, x])[0]
        if col.size == 0:
            continue
        ys = np.where(~hole[:, x])[0]
        if ys.size < 2:
            continue
        for run in _runs(col):
            lo, hi = run
            up = ys[ys < lo]
            down = ys[ys > hi]
            if up.size == 0 or down.size == 0:
                continue
            y0, y1 = int(up[-1]), int(down[0])
            for c in range(3):
                out[lo:hi + 1, x, c] = np.interp(np.arange(lo, hi + 1),
                                                 [y0, y1], [out[y0, x, c], out[y1, x, c]])
            filled[lo:hi + 1, x] = True
    if not filled[hole].all():
        med = np.median(rgb[~hole].reshape(-1, 3), axis=0) if (~hole).any() else np.array([255, 255, 255])
        left = hole & ~filled
        out[left] = med
    return np.clip(out, 0, 255).astype(np.uint8)


def _runs(idx: np.ndarray) -> list[tuple[int, int]]:
    if idx.size == 0:
        return []
    cuts = np.where(np.diff(idx) > 1)[0]
    starts = np.concatenate([[0], cuts + 1])
    ends = np.concatenate([cuts, [idx.size - 1]])
    return [(int(idx[s]), int(idx[e])) for s, e in zip(starts, ends)]

    return out


def fill_background(art: np.ndarray, mask: np.ndarray, hole: np.ndarray,
                    *, use_models: bool = True) -> tuple[np.ndarray, str]:
    """Fill `hole` from the paper around it; returns (image, method).

    Order, safest first:
      telea  OpenCV Telea: carries the paper's own texture in, cannot invent an
             object; smears fine pattern on very wide holes (documented).
      scan   row/column scanline interpolation: exact colour on one side, flat
             on the other.
      none   nothing was needed (hole is tiny) or nothing was trustworthy.

    SD inpainting is *not* used here: on an 8-step SD-1.5 pass it hallucinated a
    phone into the red paper at 8 GB, and the similarity gate could not catch it
    reliably. The generative path stays in the rigger, where the mask is small.
    """
    if not hole.any():
        return art.copy(), "none"
    # TELEA smears a large hole into light streaks (a streak on white sofa reads
    # as a halo), NS keeps structure better but is still a blur. Compare both
    # against the paper just outside the hole and keep the closer one.
    try:
        import cv2

        m8 = hole.astype(np.uint8) * 255
        bgr = cv2.cvtColor(art, cv2.COLOR_RGB2BGR)
        ring = _dilate(hole, 6) & ~hole
        ref = art[ring].astype(np.float32) if ring.any() else art[~hole].astype(np.float32)
        best, best_sim = None, -1.0
        for flag, name in ((cv2.INPAINT_NS, "ns"), (cv2.INPAINT_TELEA, "telea")):
            try:
                cand = cv2.inpaint(bgr, m8, 4, flag)
            except Exception:                          # noqa: BLE001
                continue
            rgbc = cv2.cvtColor(cand, cv2.COLOR_BGR2RGB)
            edge = rgbc[_dilate(hole, 3) & ~hole]
            if edge.size == 0 or ref.size == 0:
                continue
            sim = float(1.0 - np.abs(edge.astype(np.float32).mean(0)
                                     - ref.mean(0)).mean() / 255.0)
            if sim > best_sim:
                best, best_sim = rgbc, sim
        if best is not None:
            return best, "inpaint"
    except Exception:                                  # noqa: BLE001
        pass
    return scan_fill(art, hole), "scan"


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask)
    if not xs.size:
        return 0, 0, 0, 0
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def build(reader_dir: Path, page_id: str, art_rel: str, boxes: list[tuple],
          out_dir: Path, *, use_models: bool = True, pad: float = 0.02) -> dict:
    """Cut `art_rel` into layers around `boxes` (normalised x, y, w, h)."""
    reader_dir, out_dir = Path(reader_dir), ensure_dir(out_dir)
    art = imread(reader_dir / art_rel)
    h, w = art.shape[:2]
    keep = np.zeros((h, w), bool)
    for x, y, bw, bh in boxes:
        x0 = int(max(0, (x - pad) * w))
        y0 = int(max(0, (y - pad) * h))
        x1 = int(min(w, (x + bw + pad) * w))
        y1 = int(min(h, (y + bh + pad) * h))
        if x1 - x0 > 4 and y1 - y0 > 4:
            keep[y0:y1, x0:x1] = True
    if not keep.any():
        return {"page": page_id, "layers": [], "note": "no figure on this page"}

    # character layer: SAM outline when available (boxes are only a hint),
    # otherwise the axis-aligned figure boxes
    mask = keep
    outline = "box"
    if use_models:
        try:
            from . import sam as sam_mod

            prompt_boxes = [(max(0.0, float(x)), max(0.0, float(y)),
                             min(1.0, float(x) + float(bw)), min(1.0, float(y) + float(bh)))
                            for x, y, bw, bh in boxes]
            seg = sam_mod.segment(art, points=None, boxes=prompt_boxes)
            # SAM is trusted only when it stays inside its own prompt region;
            # otherwise it latched onto the sofa or the wallpaper. It is also
            # cleaned (open/close) so the figure's own background does not come
            # along as thin halos, and the real page edge is kept as a cap.
            keep_ref = np.zeros((h, w), bool)
            for x, y, bw, bh in prompt_boxes:
                keep_ref[int(max(0.0, y - 0.02) * h):int(min(1.0, y + bh + 0.02) * h),
                         int(max(0.0, x - 0.02) * w):int(min(1.0, x + bw + 0.02) * w)] = True
            if seg is not None:
                outside = float(seg[~keep_ref].sum()) / max(1.0, float(seg.sum()))
                if outside > 0.04:
                    seg = seg & keep_ref
                    if seg.mean() < 0.002:
                        seg = None
            if seg is not None:
                clean = _open(seg, max(2, int(0.004 * max(h, w))))
                if clean.mean() > 0.002:
                    mask = clean
                    outline = "sam"
        except Exception:                              # noqa: BLE001
            outline = "box"
    if outline == "box":
        mask = mask & _dilate(keep, 2)
    soft = _feather(_dilate(mask, 1), 0.8)
    fg_path = out_dir / f"{page_id}_fg.png"
    Image.fromarray(np.dstack([art, (soft * 255).astype(np.uint8)]), "RGBA").save(fg_path)

    # background: fill the figure's footprint with the paper that survives around
    # it. Three fillers, cheapest and safest first; the page is only marked
    # "clean" when the fill did not write anything the eye notices.
    grow = max(4, int(0.006 * max(h, w)))
    hole = _dilate(mask, grow + 10)
    if hole.mean() > 0.18:
        hole = _dilate(mask, grow + 3)   # big figures: keep the pattern next to the figure
    x0, y0, x1, y1 = _bbox(hole)
    used = "none"
    if (x1 - x0) * (y1 - y0) <= 0.22 * w * h:
        bg, used = fill_background(art, mask, hole, use_models=use_models)
    else:
        # A big knot of figures over painted wallpaper: no deterministic fill is
        # honest here (row scans smear the pattern, a colour slab reads as a
        # sticker). Keep the paper as drawn and say so, instead of inventing one.
        bg = art.copy()
    bg_path = out_dir / f"{page_id}_bg.jpg"
    Image.fromarray(bg).save(bg_path, quality=94)

    # depth decides amounts; without it use the documented defaults
    dmap = depth_mod.predict(art) if use_models else None
    depth_path = ""
    if dmap is not None:
        dp = out_dir / f"{page_id}_depth.png"
        Image.fromarray((np.clip(dmap, 0, 1) * 255).astype(np.uint8), "L").save(dp)
        depth_path = f"layers/{dp.name}"
        in_fig = float(dmap[mask].mean()) if mask.any() else 0.5
        in_bg = float(dmap[~hole].mean()) if (~hole).any() else 0.5
        near_gain = float(np.clip(0.6 + 0.8 * (in_fig - in_bg), 0.4, 1.6))
    else:
        near_gain = 1.0

    layers = [
        {"id": "bg", "image": f"layers/{bg_path.name}", "z": 0,
         "parallax": round(FAR_SHIFT * near_gain, 4), "zoom": FAR_ZOOM,
         "anchor": [0.5, 0.5]},
        {"id": "fg", "image": f"layers/{fg_path.name}", "z": 1,
         "parallax": round(NEAR_SHIFT * near_gain, 4), "zoom": 1.0,
         "anchor": [0.5, 0.5]},
    ]
    data = {"page": page_id, "art": art_rel, "art_w": w, "art_h": h,
            "depth": depth_path, "inpaint": used, "outline": outline,
            "near_gain": round(near_gain, 3), "layers": layers}
    write_json(out_dir / f"{page_id}.json", data)
    return data
