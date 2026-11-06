"""Critic: numbers that say whether the rig and the motion are usable.

Rules first, model later. Each metric is cheap, explainable and independent, so
a failure can point at one agent ("the cut grabbed background" = Rigger,
"the arm swings 40 degrees" = MotionDirector) instead of "looks bad".
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from . import imagekit as K
from .contracts import Critique, Rig

FACE_MIN_RIG = 0.98
FACE_MIN_PORTRAIT = 0.30
BG_MAX = 0.08
SEAM_MAX = 2.0
HOLE_MAX = 0.02
ANIMALS = ("豚鼠", "狗", "猫", "兔", "猪", "熊", "象", "鼠", "鸟", "鱼", "马", "羊",
           "牛", "鸡", "鸭", "鹅", "猴", "虎", "狮", "龟", "虫", "蝴蝶", "青蛙")


def _is_animal(card: dict) -> bool:
    text = f"{card.get('name', '')} {card.get('role', '')} {card.get('desc', '')}"
    return any(a in text for a in ANIMALS)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def _open(mask: np.ndarray, size: int) -> np.ndarray:
    k = max(3, int(size) | 1)
    im = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    return np.asarray(im.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))) > 127


def face_completeness(rgb: np.ndarray, alpha: np.ndarray | None = None,
                      *, has_head: bool = True) -> tuple[float, dict]:
    """Eyes + mouth + chin, weighted so 1.0 needs all three."""
    mask = np.ones(rgb.shape[:2], bool) if alpha is None else alpha > 127
    detail: dict = {"eyes": 0.0, "mouth": 0.0, "chin": 0.0}
    if int(mask.sum()) < 200:
        return 0.0, detail
    ys, xs = np.where(mask)
    h, w = mask.shape[:2]
    grey = _luma(rgb)
    inside = grey[mask]
    dark = (grey < 0.55 * float(np.median(inside))) & mask
    dark = _open(dark, max(3, min(h, w) // 40))
    blobs = [c for c in K.components(dark, min_area=0.0015, limit=16)
             if 0.3 <= (c.w / max(1, c.h)) <= 3.0]
    total = float(mask.sum())
    blobs = [c for c in blobs if 0.001 * total <= c.area <= 0.10 * total]

    # two blobs side by side near the top of the mask = eyes
    top = float(ys.min())
    head_h = max(1.0, float(ys.max() - ys.min()))
    upper = [c for c in blobs if (c.box[1] - top) / head_h < 0.70 and c.area < 0.05 * total]
    best = 0.0
    for i, a in enumerate(upper):
        for b in upper[i + 1:]:
            ax, ay = (a.box[0] + a.box[2]) / 2, (a.box[1] + a.box[3]) / 2
            bx, by = (b.box[0] + b.box[2]) / 2, (b.box[1] + b.box[3]) / 2
            if abs(ay - by) < 0.10 * head_h and abs(ax - bx) > 0.12 * w:
                ratio = min(a.area, b.area) / max(a.area, b.area)
                best = max(best, float(ratio))
    detail["eyes"] = 1.0 if best > 0.45 else 0.0

    lower = [c for c in blobs if (c.box[1] - top) / head_h > 0.55 and c.w > c.h * 1.15]
    detail["mouth"] = 1.0 if lower else 0.0
    if has_head:
        detail["chin"] = 1.0 if (ys.max() < h - 2 and xs.min() > 0 and xs.max() < w - 1) else 0.0
        score = 0.5 * detail["eyes"] + 0.3 * detail["mouth"] + 0.2 * detail["chin"]
    else:
        score = 0.7 * detail["eyes"] + 0.3 * detail["mouth"]
    return float(score), detail


def edge_background(rgb: np.ndarray, alpha: np.ndarray, key: np.ndarray,
                    band: int = 8, tol: float = 45.0) -> float:
    """Share of background-coloured pixels inside the outer `band` of the cut."""
    mask = alpha > 127
    if not mask.any():
        return 1.0
    k = (int(band) * 2 + 1) | 1
    eroded = np.asarray(Image.fromarray((mask.astype(np.uint8) * 255), "L")
                        .filter(ImageFilter.MinFilter(k))) > 127
    edge = np.logical_and(mask, ~eroded)
    if not edge.any():
        return 0.0
    return float(K.near_colour(rgb, key, tol)[edge].mean())


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Background pixels fully enclosed by `mask`."""
    outside = ~mask
    small, _s = K._small(outside, 200)
    reach = K._flood_from_border(small)
    if reach.shape != mask.shape:
        reach = np.asarray(Image.fromarray((reach * 255).astype(np.uint8), "L")
                           .resize((mask.shape[1], mask.shape[0]), Image.BILINEAR)) > 100
    return ~mask & ~reach


def seam_metrics(body: np.ndarray, part_rgba: np.ndarray, pivot: tuple[float, float],
                 key: np.ndarray, angle: float = 20.0) -> dict:
    """Rotate the part and measure the joint: holes opened, edge contrast change."""
    h, w = body.shape[:2]
    part = Image.fromarray(part_rgba, "RGBA")
    rest_mask = part_rgba[..., 3] > 127
    rot = np.asarray(part.rotate(-angle, center=(pivot[0] * w, pivot[1] * h),
                                 resample=Image.BICUBIC))
    rot_mask = rot[..., 3] > 127
    body_fg = K.colour_distance(body, key) > 30

    union = np.logical_or(body_fg, rot_mask)
    rest_union = np.logical_or(body_fg, rest_mask)
    new_holes = np.logical_and(_fill_holes(union), ~_fill_holes(rest_union))
    hole_frac = float(new_holes.sum()) / max(1.0, float(union.sum()))

    grey = _luma(np.asarray(Image.fromarray(body).convert("RGB")))
    gy, gx = np.gradient(grey)
    grad = np.hypot(gx, gy)
    rest_edge = K.ring(rest_mask, 2) & body_fg
    rot_edge = K.ring(rot_mask, 2) & body_fg
    rest = float(grad[rest_edge].mean()) if rest_edge.any() else 0.0
    seam = float(grad[rot_edge].mean()) if rot_edge.any() else 0.0
    ratio = seam / max(1e-3, rest)
    return {"angle": angle, "hole": round(hole_frac, 4), "seam": round(ratio, 3)}


def motion_naturalness(plan: dict) -> tuple[float, list[str]]:
    """Every beat inside the comfort range (hand 8-25 deg, head 2-8 deg, 1-3 Hz)."""
    beats = plan.get("beats", [])
    if not beats:
        return 1.0, []
    total = float(plan.get("duration", 3.0))
    bad: list[str] = []
    good = 0
    for b in beats:
        act = b.get("action")
        t, dur = float(b.get("t", 0)), max(0.1, float(b.get("dur", 1.0)))
        amp, cycles = float(b.get("amp", 0)), float(b.get("cycles", 1))
        ok = 0.0 <= t < total
        if act == "wave":
            ok = ok and 8.0 <= amp <= 25.0 and 1.0 <= cycles <= 3.0 and 1.0 <= cycles / dur <= 3.2
        elif act == "nod":
            ok = ok and 2.0 <= amp <= 8.0
        elif act == "bounce":
            ok = ok and 2.0 <= amp <= 6.0
        elif act in ("point", "shrug"):
            ok = ok and 5.0 <= amp <= 20.0
        if ok:
            good += 1
        else:
            bad.append(f"{act}@t={t}: amp {amp}, cycles {cycles}, dur {dur}")
    return good / float(len(beats)), bad


def text_coverage(book: dict, cards: list[dict], *, min_mentions: int = 3) -> tuple[float, list[str]]:
    """Main characters in the text must exist on the character wall."""
    text = "".join(p.get("text", "") for p in book.get("pages", []))
    main = [(c["name"], text.count(c["name"])) for c in cards if c.get("name")]
    main = [(n, k) for n, k in main if k >= min_mentions]
    if not main:
        return 1.0, []
    missing = [n for n, _ in main if not any(c.get("name") == n and c.get("pages") for c in cards)]
    return (len(main) - len(missing)) / float(len(main)), missing


def judge_character(reader_dir, card: dict, book: dict, *, rig: Rig | None = None) -> Critique:
    from pathlib import Path

    from ..util import imread

    cid = card["id"]
    rig = rig or Rig.from_dict(card.get("rig") or {})
    scores: dict[str, float] = {}
    reasons: list[str] = []
    accept = True

    portrait = card.get("portrait") or ""
    animal = _is_animal(card)
    head = next((p for p in rig.parts if p.name == "head"), None)
    if head:
        rgba = np.asarray(Image.open(Path(reader_dir) / head.image).convert("RGBA"))
        score, detail = face_completeness(rgba[..., :3], rgba[..., 3], has_head=True)
        scores["face_completeness"] = score
        if detail["eyes"] < 1.0:
            accept = False
            reasons.append("the head cut does not contain both eyes")
        elif score < FACE_MIN_RIG:
            reasons.append(f"face completeness {score:.2f} (mouth/chin soft)")
    elif portrait and not animal:
        rgba = imread(Path(reader_dir) / portrait)
        score, detail = face_completeness(rgba, has_head=False)
        scores["face_completeness"] = score
        if score < FACE_MIN_PORTRAIT:
            accept = False
            reasons.append(f"portrait has no readable face ({score:.2f} < {FACE_MIN_PORTRAIT})")
    elif rig.art and not animal:
        # a cut puppet must still be a character: its upper half needs a face,
        # otherwise the rigger latched onto a photo frame or a prop
        plate = imread(Path(reader_dir) / rig.art)
        top = plate[: max(8, int(plate.shape[0] * 0.60))]
        score, detail = face_completeness(top, has_head=False)
        scores["cut_face"] = score
        if detail["eyes"] < 1.0:
            accept = False
            reasons.append("the cut region has no face; not a character figure")

    art_rel = rig.art or ""
    if art_rel and rig.parts:
        art = imread(Path(reader_dir) / art_rel)
        body = art
        for p in rig.parts:
            if not p.name.startswith("arm_"):
                continue
            part = np.asarray(Image.open(Path(reader_dir) / p.image).convert("RGBA"))
            key, _ = K.border_key(art)
            res = edge_background(part[..., :3], part[..., 3], key)
            scores[f"bg_residue_{p.name}"] = res
            if res > BG_MAX:
                accept = False
                reasons.append(f"{p.name} keeps {res:.0%} background in its edge band")
            seam = seam_metrics(body, part, p.pivot, key)
            scores[f"seam_{p.name}"] = seam["seam"]
            scores[f"hole_{p.name}"] = seam["hole"]
            if seam["hole"] > HOLE_MAX:
                accept = False
                reasons.append(f"{p.name} opens a {seam['hole']:.1%} hole at the joint "
                               f"({seam['angle']:.0f} deg)")
            if seam["seam"] > SEAM_MAX:
                reasons.append(f"{p.name} seam contrast x{seam['seam']:.1f} "
                               f"(soft: overlaps instead of cutting)")

    scores["rig_confidence"] = rig.confidence
    if rig.kind == "paper_puppet" and rig.confidence < 0.55:
        reasons.append(f"rig confidence {rig.confidence:.2f} is low")

    return Critique(target=cid, accept=accept, scores=scores, reasons=reasons)


def judge_motion(plans: dict[str, dict]) -> list[Critique]:
    out: list[Critique] = []
    for page, plan in plans.items():
        score, bad = motion_naturalness(plan)
        out.append(Critique(target=page, accept=score >= 0.99,
                            scores={"motion_naturalness": score}, reasons=bad))
    return out
