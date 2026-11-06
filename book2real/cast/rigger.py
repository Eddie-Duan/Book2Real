"""Rigger: cut one character into rigid paper-puppet parts.

The puppet is deliberately rigid: a body plate plus arms that rotate around a
shoulder (or wrist) pivot. Nothing is mesh-warped, so the drawing can never
wobble. The rigger only trusts geometry it can measure — the neck from the
row-width profile, the torso from the widest run under it, the arms from the
silhouette that hugs that torso — and reports a confidence so the Critic (or a
human) can fall back to a plain portrait instead of shipping a bad joint.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ..util import imread
from . import imagekit as K
from .contracts import Rig, RigPart

HEAD_AMP = 4.0
LONG_ARM = 0.28            # arm span (of body height) above which it swings at the top
SHOULDER_CAP = 0.75        # share of the part kept in the body as a joint cap

_PAGES: dict[str, tuple] = {}      # one rebuilt analysis per art file, per run


def _analyse(reader_dir: Path, art_rel: str, key_tol: float):
    """Art, key colour, alpha and figure components — cached per file."""
    path = Path(reader_dir) / art_rel
    stamp = f"{path}:{path.stat().st_mtime_ns}:{key_tol}"
    hit = _PAGES.get(stamp)
    if hit is not None:
        return hit
    if len(_PAGES) > 4:
        _PAGES.clear()
    art = imread(path)
    key, _spread = K.border_key(art)
    alpha, sep = K.key_alpha(art, key, key_tol)
    comps = K.components(alpha > 127, min_area=0.004, limit=8)
    _PAGES[stamp] = (art, key, alpha, sep, comps)
    return _PAGES[stamp]


def _row_runs(row: np.ndarray) -> list[tuple[int, int]]:
    idx = np.where(row)[0]
    if idx.size == 0:
        return []
    cuts = np.where(np.diff(idx) > 1)[0]
    starts = np.concatenate([[0], cuts + 1])
    ends = np.concatenate([cuts, [idx.size - 1]])
    return [(int(idx[s]), int(idx[e])) for s, e in zip(starts, ends)]


def neck_row(mask: np.ndarray, top: int, bottom: int) -> int | None:
    """Row where the head narrows into the neck, or None for a close-up."""
    h = bottom - top + 1
    if h < 40:
        return None
    widths = np.array([mask[y].sum() for y in range(top, bottom + 1)], np.float32)
    head_end = int(0.35 * h)
    if head_end < 8:
        return None
    peak = int(np.argmax(widths[:head_end]))
    if peak < 4:
        return None
    limit = 0.82 * float(widths[peak])
    for y in range(peak + 4, max(peak + 5, head_end - 4)):
        if widths[y] < limit and float(widths[y:y + 4].max()) < limit:
            return top + y
    return None


def _open(mask: np.ndarray, radius: int) -> np.ndarray:
    """Morphological opening with a square of `radius`; removes parts thinner than 2r."""
    r = max(1, int(radius))
    if K.cv2 is not None:
        k = np.ones((2 * r + 1, 2 * r + 1), np.uint8)
        m = np.ascontiguousarray(mask.astype(np.uint8))
        return K.cv2.morphologyEx(m, K.cv2.MORPH_OPEN, k).astype(bool)
    k = max(3, int(2 * r + 1))
    im = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    return np.asarray(im.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))) > 127


def core_edges(core: np.ndarray, band: tuple[int, int]) -> tuple[int, int]:
    """Left/right edge of the body *without* its thin parts (opening removes arms)."""
    y0, y1 = band
    any_row = core[y0:y1]
    if not any_row.any():
        ys, xs = np.where(core)
        if len(xs) == 0:
            return 0, 0
        return int(xs.min()), int(xs.max())
    lefts, rights = [], []
    for y in range(0, any_row.shape[0], 2):
        runs = _row_runs(any_row[y])
        if not runs:
            continue
        widest = max(runs, key=lambda r: r[1] - r[0])
        lefts.append(widest[0])
        rights.append(widest[1])
    if not lefts:
        return 0, 0
    return int(np.median(lefts)), int(np.median(rights))


def _arm(mask: np.ndarray, side: str, band: tuple[int, int], neck_y: int,
         body_w: int, body_h: int, char_area: float, bottom_edge: int) -> tuple[np.ndarray, float] | None:
    """Thin protrusion beside the torso inside the shoulder band."""
    arm_radius = max(2, int(round(0.035 * body_w)))
    prot = np.logical_and(mask, ~_open(mask, arm_radius))
    prot = prot.copy()
    prot[:band[0]] = False
    prot[band[1]:] = False
    comps = K.components(prot, limit=10)          # absolute filters below, not area fraction
    ys, xs = np.where(mask)
    cx = float(np.median(xs))
    best = None
    for c in comps:
        ccx = (c.box[0] + c.box[2]) / 2.0
        if side == "l" and ccx > cx - 0.02 * body_w:
            continue
        if side == "r" and ccx < cx + 0.02 * body_w:
            continue
        if c.area < 0.003 * char_area or c.area > 0.15 * char_area:
            continue
        if c.h / max(1, c.w) < 0.45 or c.h < 0.08 * body_h or c.w > 0.40 * body_w:
            continue
        if c.box[3] > bottom_edge - 2:
            continue
        if (c.box[1] + c.box[3]) / 2.0 < neck_y:      # stays below the chin line
            continue
        if best is None or c.area > best.area:
            best = c
    if best is None:
        return None
    return best.mask, best.h / max(1.0, float(body_h))


def _erase(art: np.ndarray, region: np.ndarray, key: np.ndarray,
           feather: float = 1.2) -> np.ndarray:
    """Flat-fill a cut region with paper, keeping the page's per-row lighting.

    A single constant colour leaves a visible slab on a lit page, so the fill is
    the key colour scaled by the median paper brightness of the same rows
    (sampled ~12 px outside the region). Pure numpy: no model needed.
    """
    h, w = art.shape[:2]
    a = np.asarray(Image.fromarray((region.astype(np.uint8) * 255), "L")
                   .filter(ImageFilter.GaussianBlur(feather)), np.float32) / 255.0
    luma = art.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    key_l = float(np.asarray(key, np.float32) @ np.array([0.299, 0.587, 0.114], np.float32))
    gain = np.ones(h, np.float32)
    band = max(4, int(0.02 * max(h, w)))
    grow = np.asarray(Image.fromarray((region.astype(np.uint8) * 255), "L")
                      .filter(ImageFilter.MaxFilter(min(2 * band + 1, 2 * (h // 2) + 1))), bool) > 127
    ring = grow & ~region
    for y in range(h):
        if not region[y].any():
            continue
        above, below = ring[max(0, y - band):y], ring[y + 1:y + 1 + band]
        vals = np.concatenate([luma[max(0, y - band):y][above],
                               luma[y + 1:y + 1 + band][below]])
        if vals.size > 8:
            gain[y] = float(np.median(vals)) / max(1e-3, key_l)
    fill = np.asarray(key, np.float32)[None, None, :] * gain[:, None, None]
    out = art.astype(np.float32) * (1 - a[..., None]) + fill * a[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def _rgba(art: np.ndarray, alpha: np.ndarray, region: np.ndarray) -> np.ndarray:
    a = (alpha.astype(np.float32) / 255.0) * region.astype(np.float32)
    a = np.asarray(Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "L")
                   .filter(ImageFilter.GaussianBlur(0.7)), np.float32)
    return np.dstack([art, a.astype(np.uint8)])


def build(reader_dir: Path, out_dir: Path, cid: str, art_rel: str, box,
          *, key_tol: float = 60.0, inpaint_model: bool = False) -> Rig:
    reader_dir, out_dir = Path(reader_dir), Path(out_dir)
    if not art_rel:
        return Rig(kind="portrait", notes=["no illustration for this character"])

    art, key, alpha, sep, comps = _analyse(reader_dir, art_rel, key_tol)
    h, w = art.shape[:2]
    solid = alpha > 127

    x, y, bw, bh = (float(v) for v in box)
    box_px = (int(x * w), int(y * h), int((x + bw) * w), int((y + bh) * h))
    if not comps:
        return Rig(kind="portrait", notes=["no figure on a flat background"])
    best, best_iou = None, -1.0
    for c in comps:
        ix0, iy0 = max(c.box[0], box_px[0]), max(c.box[1], box_px[1])
        ix1, iy1 = min(c.box[2], box_px[2]), min(c.box[3], box_px[3])
        inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
        union = c.w * c.h + max(1, box_px[2] - box_px[0]) * max(1, box_px[3] - box_px[1]) - inter
        iou = inter / max(1, union)
        if iou > best_iou:
            best, best_iou = c, iou
    if best is None or best_iou <= 0:
        return Rig(kind="portrait", notes=["figure not separable from its plate"])

    mask = best.mask
    ys, xs = np.where(mask)
    m = int(round(0.08 * max(xs.max() - xs.min(), ys.max() - ys.min()) + 4))
    cx0, cy0 = max(0, int(xs.min()) - m), max(0, int(ys.min()) - m)
    cx1, cy1 = min(w, int(xs.max()) + 1 + m), min(h, int(ys.max()) + 1 + m)
    art = np.ascontiguousarray(art[cy0:cy1, cx0:cx1])
    alpha = np.ascontiguousarray(alpha[cy0:cy1, cx0:cx1])
    mask = np.ascontiguousarray(mask[cy0:cy1, cx0:cx1])
    h, w = art.shape[:2]

    ys, xs = np.where(mask)
    top, bottom = int(ys.min()), int(ys.max())
    H = bottom - top + 1
    body_w = int(xs.max() - xs.min() + 1)
    char_area = float(mask.sum())
    notes: list[str] = []

    neck = neck_row(mask, top, bottom)
    if neck is None:
        notes.append("no neck line found; head split estimated at 30% height")
    head_bottom = neck if neck else top + int(0.30 * H)
    chin = top + int(0.26 * H)
    band = (head_bottom, min(bottom, head_bottom + int(0.55 * H)))

    core = _open(mask, max(5, int(0.10 * body_w)))
    torso_l, torso_r = core_edges(core, band)
    if torso_r - torso_l < 0.15 * body_w:
        notes.append("torso narrower than the arms; cuts may be rough")

    parts: list[RigPart] = []
    body = art.copy()
    arms: list[tuple[str, np.ndarray, RigPart]] = []
    for side in ("l", "r"):
        found = _arm(mask, side, band, chin, body_w, H, char_area, bottom)
        if not found:
            notes.append(f"arm_{side}: nothing arm-shaped beside the torso")
            continue
        arm, span = found
        ays, axs = np.where(arm)
        inner = int(axs.max()) if side == "l" else int(axs.min())
        near = np.abs(axs - inner) <= 2
        py = int(np.median(ays[near])) if span <= LONG_ARM else int(ays.min())
        px = inner + (2 if side == "l" else -2)
        amp = float(np.clip(18.0 * min(1.0, 0.22 / max(0.06, span)), 8.0, 25.0))
        arms.append((side, arm, RigPart(name=f"arm_{side}",
                                        image=f"characters/{cid}_arm_{side}.png",
                                        pivot=(px / w, py / h), amp=round(amp, 1))))

    for _side, arm, _part in arms:
        body = _erase(body, arm, key)
    for _side, armmask, part in arms:
        ays, axs = np.where(armmask)
        extent = max(axs.max() - axs.min() + 1, ays.max() - ays.min() + 1)
        px = int(part.pivot[0] * w)
        py = int(part.pivot[1] * h)
        yy, xx = np.ogrid[:h, :w]
        disc = ((xx - px) ** 2 + (yy - py) ** 2) <= (max(0.05 * min(w, h),
                                                        SHOULDER_CAP * extent)) ** 2
        body[disc & mask] = art[disc & mask]

    body_path = out_dir / f"{cid}_body.jpg"
    Image.fromarray(body).save(body_path, quality=94)
    for _side, arm, part in arms:
        Image.fromarray(_rgba(art, alpha, arm), "RGBA").save(out_dir / Path(part.image).name)
        parts.append(part)

    # L2: finish the cut generatively when the flat fill is not good enough.
    # Only the erased region is regenerated; the similarity gate keeps the
    # result if it matches the paper, otherwise the flat fill stays.
    if inpaint_model and parts:
        try:
            from ..vision import inpaint as inpaint_mod

            hole = np.zeros((h, w), bool)
            for _side, armmask, _part in arms:
                hole |= armmask
            if hole.any():
                filled, info = inpaint_mod.inpaint(body, hole,
                                                   key=np.asarray(key, np.float32))
                if info.get("accept") and info.get("used") == "sd":
                    body = filled
                    Image.fromarray(body).save(body_path, quality=94)
                notes.append(f"erase -> {info.get('used')} (sim {info.get('sim')})")
        except Exception as exc:                      # noqa: BLE001
            notes.append(f"erase stays flat ({exc.__class__.__name__})")

    if neck and top > int(0.02 * h) and neck - top > 0.10 * H:
        head = np.zeros((h, w), bool)
        head[top:neck, :] = mask[top:neck, :]
        if head.sum() > 0.02 * char_area:
            hy, hx = np.where(head)
            pivot = (float(np.median(hx)) / w, float(hy.max()) / h)
            Image.fromarray(_rgba(art, alpha, head), "RGBA").save(out_dir / f"{cid}_head.png")
            parts.append(RigPart(name="head", image=f"characters/{cid}_head.png",
                                 pivot=pivot, amp=HEAD_AMP))

    sep_score = min(1.0, sep / 120.0)
    arm_score = 1.0 if len(arms) == 2 else (0.8 if arms else 0.0)
    geom = 1.0 if neck is not None else 0.65
    conf = 0.25 + 0.30 * sep_score + 0.25 * arm_score + 0.20 * geom
    if len(arms) == 2:
        kind = "paper_puppet"
    elif arms:
        kind = "one_arm"
    elif any(p.name == "head" for p in parts):
        kind = "head_nod"
    else:
        kind = "portrait"
        notes.append("no arm or head cut; the stage shows a plain portrait")

    return Rig(kind=kind, confidence=round(min(0.95, conf), 3),
               art=f"characters/{body_path.name}", art_w=w, art_h=h,
               key=tuple(int(round(v)) for v in key),
               parts=parts, notes=notes)


def preview(reader_dir: Path, out_dir: Path, rig: Rig, cid: str) -> str:
    """Render the rig at rest and with every arm swung, for docs / eyeballing."""
    if not rig.parts or not rig.art:
        return ""
    body = Image.open(Path(reader_dir) / rig.art).convert("RGB")
    frames = []
    for angle in (0, 20, -20):
        canvas = body.copy()
        for p in rig.parts:
            if not p.name.startswith("arm_"):
                continue
            part = Image.open(Path(reader_dir) / p.image).convert("RGBA")
            deg = -angle if p.name.endswith("r") else angle
            rot = part.rotate(deg, center=(p.pivot[0] * part.width, p.pivot[1] * part.height),
                              resample=Image.BICUBIC)
            canvas.paste(rot, (0, 0), rot)
        frames.append(canvas.resize((canvas.width // 2, canvas.height // 2)))
    sheet = Image.new("RGB", (frames[0].width * len(frames), frames[0].height), (245, 243, 238))
    for i, f in enumerate(frames):
        sheet.paste(f, (i * f.width, 0))
    path = out_dir / f"{cid}_preview.jpg"
    sheet.save(path, quality=90)
    return f"characters/{path.name}"
