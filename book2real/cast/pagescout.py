"""PageScout: what is actually drawn on this page?

Input : the reader package (cleaned art + narration text) and curation.json.
Output: per page, up to four figure-ish entities with a normalised box, a
        colour signature, a proposed name and a confidence.

Naming is deliberately conservative. A page that mentions exactly one known
character names its largest figure; a page that mentions two names and shows
two figures pairs them by area, but with low confidence. Everything else stays
anonymous so CastClerk can decide, and a page without a separable figure
degrades to "OCR text only" instead of inventing a character.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..util import imread
from . import imagekit as K
from .contracts import Entity

ANIMALS = ("豚鼠", "狗", "猫", "兔", "猪", "熊", "象", "鼠", "鸟", "鱼", "马", "羊",
           "牛", "鸡", "鸭", "鹅", "猴", "虎", "狮", "龟", "虫", "蝴蝶", "青蛙")
ROLES = ("妈妈", "爸爸", "爷爷", "奶奶", "外婆", "外公", "哥哥", "姐姐", "弟弟", "妹妹",
         "老师", "女孩", "男孩", "小朋友", "孩子")


def _kind(name: str, text: str) -> str:
    for w in ANIMALS:
        if w in name:
            return "animal"
    if name and any(r in text for r in ROLES):
        return "character"
    return "character" if name else "unknown"


def _dominant_colours(rgb: np.ndarray, mask: np.ndarray, k: int = 3) -> list[list[int]]:
    px = rgb[mask]
    if len(px) < 32:
        return []
    step = max(1, len(px) // 4000)
    sample = px[::step].astype(np.float32)
    # deterministic k-means: seed on luminance quantiles
    lum = sample.mean(axis=1)
    order = np.argsort(lum)
    centres = np.stack([sample[order[int(q * (len(order) - 1))]] for q in
                        np.linspace(0.15, 0.85, k)])
    for _ in range(6):
        d = ((sample[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        lab = d.argmin(axis=1)
        for j in range(k):
            sel = lab == j
            if sel.any():
                centres[j] = sample[sel].mean(axis=0)
    counts = np.bincount(lab, minlength=k)
    out = [centres[j] for j in np.argsort(-counts)]
    return [[int(round(c)) for c in col] for col in out]


def _box_overlap(a, b) -> float:
    """Share of box `a` covered by box `b` (normalised xywh)."""
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    return (ix * iy) / max(1e-6, a[2] * a[3])


def _spot_name(box, spots: list[dict]) -> tuple[str, float]:
    """A curated spot that covers this blob is the most reliable identity we have."""
    hits = [(float(_box_overlap(box, s["box"])), s.get("name", "")) for s in spots
            if s.get("box") and s.get("name")]
    hits = [h for h in hits if h[0] >= 0.5]
    if len(hits) != 1:                                  # none, or two names on one blob
        return "", 0.0
    return hits[0][1], hits[0][0]


def _shape_ok(name: str, role_text: str, box, art_w: int, art_h: int) -> bool:
    """A guinea pig is wide and a child is tall; a mismatch means the name is wrong."""
    w = box[2] * art_w
    h = box[3] * art_h
    if w <= 0 or h <= 0:
        return False
    animal = any(a in name or a in role_text for a in ANIMALS)
    if animal:
        return (w / h) >= 1.15 or box[2] * box[3] < 0.06
    return (w / h) <= 1.9


def _mentioned(text: str, names: list[str]) -> list[str]:
    hits = [(text.find(n), n) for n in names if n and n in text]
    hits.sort()
    seen, out = set(), []
    for _, n in hits:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def scan_page(art_path: Path, page_ids: list[str], text: str, *,
              known: dict[str, str], roles: dict[str, str] | None = None,
              spots: list[dict] | None = None, max_entities: int = 4,
              tol: float = 70.0) -> dict:
    """Find the figures on one illustration and propose names for them."""
    rgb = imread(art_path)
    h, w = rgb.shape[:2]
    key, spread = K.border_key(rgb)
    dist = K.colour_distance(rgb, key)
    fg = dist > max(tol, spread * 4.0)
    comps = K.components(fg, min_area=0.012, max_area=0.92, limit=10)

    candidates: list[tuple[K.Component, dict]] = []
    for c in comps:
        wf, hf = c.w / w, c.h / h
        af = c.area / float(h * w)
        fill = c.area / float(max(1, c.w * c.h))
        aspect = c.w / float(max(1, c.h))
        if wf > 0.94 and hf > 0.94:
            continue                                     # the whole plate
        if af > 0.72 or hf < 0.10 or wf < 0.03:
            continue
        if aspect > 5.0 or aspect < 0.15 or fill < 0.15:
            continue
        lum = rgb[c.mask].astype(np.float32).mean(axis=1)
        if float(lum.std()) < 8.0:
            continue                                     # a flat panel, not a figure
        candidates.append((c, {"wf": wf, "hf": hf, "af": af, "fill": fill,
                               "aspect": aspect}))
    candidates.sort(key=lambda t: t[0].area, reverse=True)
    candidates = candidates[:max_entities]

    mentioned = _mentioned(text, list(known))
    entities: list[Entity] = []
    for rank, (comp, feat) in enumerate(candidates):
        eid = f"{page_ids[0]}-e{rank + 1}"
        box = (comp.box[0] / w, comp.box[1] / h,
               (comp.box[2] - comp.box[0]) / w, (comp.box[3] - comp.box[1]) / h)
        sep = float(np.median(dist[comp.mask]))
        conf = 0.30 + 0.20 * min(1.0, sep / 120.0) + 0.15 * min(1.0, feat["fill"] / 0.5)
        if spread < 25:
            conf += 0.10
        conf = min(0.9, conf)
        entities.append(Entity(
            id=eid, page=page_ids[0], box=box, area=round(feat["af"], 4),
            aspect=round(feat["aspect"], 3), fill=round(feat["fill"], 3),
            colors=_dominant_colours(rgb, comp.mask),
            signature=[round(float(v), 5) for v in K.colour_signature(rgb, comp.mask)],
            importance=round(1.0 / (1 + rank * 0.6), 3), confidence=round(conf, 3),
        ))

    # --- naming: curated spots first, then only unambiguous narration
    roles = roles or {}
    spots = spots or []
    for e in entities:
        name, frac = _spot_name(e.box, spots)
        if name:
            e.name = name
            e.name_source = "spot"
            e.confidence = min(0.97, round(e.confidence + 0.25, 3))
            e.label = f"{frac:.0%} of the blob is a curated spot"

    unnamed = [e for e in entities if not e.name]
    mentioned = _mentioned(text, list(known))
    # one figure + one name is evidence; matching several names to several blobs
    # by size is a guess, so those stay anonymous for CastClerk / a human
    if len(unnamed) == 1 and len(mentioned) == 1 and unnamed[0].area <= 0.42:
        e = unnamed[0]
        if _shape_ok(mentioned[0], roles.get(mentioned[0], ""), e.box, w, h):
            e.name = mentioned[0]
            e.name_source = "text"
            e.confidence = min(0.9, round(e.confidence + 0.15, 3))

    for e in entities:
        e.kind = _kind(e.name, text) if e.name else ("object" if e.area < 0.05 else "character")
        e.desc = ""

    return {
        "art": str(art_path.name), "art_w": w, "art_h": h,
        "key": [int(round(v)) for v in key], "spread": round(spread, 2),
        "degraded": not entities,
        "entities": [e.to_dict() for e in entities],
    }


def scan(book: dict, book_dir: Path, curation: dict | None = None) -> dict:
    """Run PageScout over a reader package."""
    curation = curation or {}
    book_dir = Path(book_dir)
    reader_dir = book_dir / "reader"

    known: dict[str, str] = {}
    roles: dict[str, str] = {}
    for ch in curation.get("characters", []) + book.get("characters", []):
        name = ch.get("name") or ""
        if name:
            known[name] = ch.get("id") or name
            roles[name] = f"{ch.get('role', '')} {ch.get('desc', '')}"

    # one art file can back several reader screens (a long page split in two);
    # the cast chain always looks at the full page, not the per-screen crop
    by_art: dict[str, list[dict]] = {}
    for p in book.get("pages", []):
        if p.get("kind") == "cover":
            continue
        by_art.setdefault(p.get("art_page") or p["art"], []).append(p)

    pages: dict[str, dict] = {}
    degraded: list[str] = []
    for art, plist in by_art.items():
        text = "".join(p.get("text", "") for p in plist)
        ids = [p["id"] for p in plist]
        page_key = ids[0].split("-")[0]
        spots = (curation.get("pages", {}).get(page_key) or {}).get("spots", [])
        found = scan_page(reader_dir / art, ids, text, known=known, roles=roles,
                          spots=spots)
        for pid in ids:
            entry = dict(found)
            entry["entities"] = [dict(e, id=e["id"].replace(ids[0], pid, 1),
                                      page=pid) for e in found["entities"]]
            pages[pid] = entry
            if entry["degraded"]:
                degraded.append(pid)

    return {
        "agent": "PageScout",
        "known_names": known,
        "pages": pages,
        "degraded_pages": degraded,
        "notes": [
            "identity comes from curated spots first, then from narration that "
            "names exactly one character whose shape matches the blob",
            "art labels are not mapped back to page pixels yet",
        ],
    }


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
