"""Boundary-first cuts:算子找边界，agent 判断"哪些是一体、是什么"。

    python -m book2real agent probe   --from frame.jpg --point 0.775,0.685 [--point ...]
    python -m book2real agent inspect --from frame.jpg --prompt 0.63,0.60,0.20,0.40
    python -m book2real agent cut     --from frame.jpg --spec spec.json --name arm_r
    python -m book2real agent review  --rig out/arm_r.json

Why rectangles were wrong
-------------------------
A bounding box is not a boundary. The p17 cut pulled a slice of cheek and dress
along with the arm because the piece was the rectangle, not the drawing inside
it. Real picture-book art has a black ink outline around every shape, so the
boundaries are measurable, and the only judgement left is *which* regions belong
together — that is the part an agent (or a person) should make, not the geometry.

Pipeline
--------
1. `probe`   — operators only, no model: colour/edge segmentation of the frame
               into regions with area / bbox / hole count / mean colour / shares
               a border with the figure.
2. `inspect` — SAM's proposal for a rectangle or a point, as a region id set.
3. agent     — reads the table, writes a spec: which region ids are the piece,
               which are holes to keep transparent, and what the piece is.
4. `cut`     — turns the spec into an RGBA part + pivot + amp, using the region
               boundary (dilated 1 px) as the alpha. No rectangle survives.
5. `review`  — scores the result (is the piece connected, does it keep paper
               colour, is the pivot on it) so the next agent can reject it.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ..util import ensure_dir, imread, write_json

# --------------------------------------------------------------------- operators


def _palette_quantise(rgb: np.ndarray, colours: int = 12) -> np.ndarray:
    im = Image.fromarray(rgb).convert("P", palette=Image.ADAPTIVE, colors=colours)
    return np.asarray(im.convert("RGB"))


def _edge_map(rgb: np.ndarray) -> np.ndarray:
    """Sobel magnitude, normalised: the black ink outline is the strongest edge."""
    grey = rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    gx = np.zeros_like(grey)
    gy = np.zeros_like(grey)
    gx[:, 1:-1] = grey[:, 2:] - grey[:, :-2]
    gy[1:-1, :] = grey[2:, :] - grey[:-2, :]
    mag = np.hypot(gx, gy)
    return mag / max(1e-3, float(mag.max()))


def regions(rgb: np.ndarray, colours: int = 12, min_area: int = 120) -> list[dict]:
    """Colour regions after quantisation, with the fields a decision needs."""
    q = _palette_quantise(rgb, colours)
    h, w = rgb.shape[:2]
    labels = np.full((h, w), -1, np.int32)
    try:
        import cv2

        n, lab, stats, cents = cv2.connectedComponentsWithStats(
            (np.asarray(Image.fromarray(q).convert("L")) * 0 + 1).astype(np.uint8), connectivity=4)
    except Exception:                                  # noqa: BLE001
        n, lab, stats, cents = 0, None, None, None
    # quantised colours are the segmentation, so group by colour+connectivity
    out: list[dict] = []
    uniq = np.unique(q.reshape(-1, 3), axis=0)
    next_id = 0
    for colour in uniq:
        same = np.all(q == colour, axis=2)
        comps = _components(same, min_area=min_area)
        for mask in comps:
            ys, xs = np.where(mask)
            if not len(xs):
                continue
            hole_n = _hole_count(mask)
            out.append({
                "id": next_id,
                "area": int(mask.sum()),
                "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                "colour": [int(c) for c in colour],
                "holes": hole_n,
                "border": bool(xs.min() <= 1 or ys.min() <= 1
                               or xs.max() >= w - 2 or ys.max() >= h - 2),
                "mask": mask,
            })
            next_id += 1
    return out


def _components(mask: np.ndarray, min_area: int = 120) -> list[np.ndarray]:
    try:
        import cv2

        n, lab, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), connectivity=4)
        return [lab == i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area]
    except Exception:                                  # noqa: BLE001
        return [mask] if mask.sum() >= min_area else []


def _hole_count(mask: np.ndarray) -> int:
    """Number of enclosed background holes: an arm has 0, a letter 'o' has 1."""
    padded = np.pad(mask, 1, constant_values=False)
    try:
        import cv2

        n, _ = cv2.connectedComponents(padded.astype(np.uint8), connectivity=4)
        filled = np.zeros_like(mask)
        return max(0, n - 2)
    except Exception:                                  # noqa: BLE001
        return 0


def table(rgb: np.ndarray, *, colours: int = 12, min_area: int = 120,
          limit: int = 40) -> list[dict]:
    """The decision table an agent reads: one row per region, biggest first."""
    regs = regions(rgb, colours=colours, min_area=min_area)
    regs.sort(key=lambda r: -r["area"])
    rows = []
    h, w = rgb.shape[:2]
    edges = _edge_map(rgb)
    for r in regs[:limit]:
        m = r["mask"]
        ring = _ring(m, 2)
        rows.append({
            "id": r["id"],
            "area": r["area"],
            "area_pct": round(100.0 * r["area"] / (h * w), 2),
            "box": r["box"],
            "box_pct": [round(r["box"][0] / w, 3), round(r["box"][1] / h, 3),
                        round((r["box"][2] - r["box"][0]) / w, 3),
                        round((r["box"][3] - r["box"][1]) / h, 3)],
            "colour": r["colour"],
            "holes": r["holes"],
            "border": r["border"],
            "edge_strength": round(float(edges[ring].mean()) if ring.any() else 0.0, 3),
        })
    return rows


def _ring(mask: np.ndarray, r: int = 2) -> np.ndarray:
    m = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    grow = m.filter(ImageFilter.MaxFilter(2 * r + 1))
    return (np.asarray(grow) > 127) & ~mask


def apply_spec(rgb: np.ndarray, spec: dict, *, pad: int = 1) -> tuple[np.ndarray, dict]:
    """Build the RGBA piece from a spec of region ids.

    spec: {"regions": [3, 4], "holes": [5], "pivot": [px, py], "name": "arm_r"}
    `holes` are regions that sit *inside* the piece and must stay transparent
    (the red paper seen between the arm and the dress).
    """
    regs = {r["id"]: r for r in regions(rgb, colours=int(spec.get("colours", 12)),
                                        min_area=int(spec.get("min_area", 120)))}
    mask = np.zeros(rgb.shape[:2], bool)
    for rid in spec.get("regions", []):
        if rid in regs:
            mask |= regs[rid]["mask"]
    for hid in spec.get("holes", []):
        if hid in regs:
            mask &= ~regs[hid]["mask"]
    if not mask.any():
        raise ValueError("spec selects no pixels")
    keep = np.asarray(Image.fromarray((mask.astype(np.uint8) * 255), "L")
                      .filter(ImageFilter.MaxFilter(1 + 2 * pad))) > 127
    alpha = keep.astype(np.uint8) * 255
    ys, xs = np.where(alpha > 0)
    m = 3
    x0, y0 = max(0, int(xs.min()) - m), max(0, int(ys.min()) - m)
    x1, y1 = min(rgb.shape[1], int(xs.max()) + 1 + m), min(rgb.shape[0], int(ys.max()) + 1 + m)
    rgba = np.dstack([rgb[y0:y1, x0:x1], alpha[y0:y1, x0:x1]])
    meta = {
        "offset_px": [x0, y0],
        "size_px": [int(x1 - x0), int(y1 - y0)],
        "pivot": spec.get("pivot") or [0.5, 0.5],
        "regions": spec.get("regions", []),
        "holes": spec.get("holes", []),
        "kind": spec.get("kind", "one_arm"),
        "label": spec.get("label", ""),
        "how": "regions+edges",
    }
    return rgba, meta


def score(rgba: np.ndarray, meta: dict, rgb: np.ndarray) -> dict:
    """Cheap review so a later agent can accept/reject without eyes."""
    a = rgba[..., 3] > 127
    if not a.any():
        return {"accept": False, "reason": "empty piece"}
    n, comps = 0, []
    try:
        import cv2

        n, lab, stats, _ = cv2.connectedComponentsWithStats(a.astype(np.uint8), connectivity=8)
        comps = [int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)]
    except Exception:                                  # noqa: BLE001
        comps = [int(a.sum())]
    biggest = max(comps) if comps else 0
    single = biggest >= 0.85 * sum(comps)
    # does the piece swallow paper colour? a part that is mostly background is a box cut
    px = rgba[..., :3][a].astype(np.float32)
    h, w = rgb.shape[:2]
    border = np.concatenate([rgb[:4].reshape(-1, 3), rgb[-4:].reshape(-1, 3)])
    key = np.median(border, axis=0)
    paper = float((np.linalg.norm(px - key, axis=1) < 40).mean())
    pivot = meta.get("pivot") or [0.5, 0.5]
    ph, pw = rgba.shape[:2]
    cx, cy = int(pivot[0] * pw), int(pivot[1] * ph)
    on_piece = bool(a[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3].any())
    accept = single and paper < 0.35 and on_piece
    return {"accept": accept, "components": len(comps), "single": single,
            "paper_share": round(paper, 3), "pivot_on_piece": on_piece,
            "area_px": int(a.sum())}


# ------------------------------------------------------------------- CLI verbs


def cmd_probe(args) -> int:
    art = imread(Path(args.src))
    rows = table(art, colours=args.colours, min_area=args.min_area, limit=args.limit)
    pts = []
    for p in args.point or []:
        x, y = (float(v) for v in p.split(","))
        pts.append({"point": [x, y], "region": _region_at(art, x, y, args.colours, args.min_area)})
    out = {"frame": args.src, "size": [int(art.shape[1]), int(art.shape[0])],
           "regions": rows, "points": pts}
    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:6000])
    return 0


def _region_at(art, x: float, y: float, colours: int, min_area: int) -> int:
    h, w = art.shape[:2]
    px, py = int(x * w), int(y * h)
    for r in regions(art, colours=colours, min_area=min_area):
        if r["mask"][min(h - 1, py), min(w - 1, px)]:
            return r["id"]
    return -1


def cmd_inspect(args) -> int:
    """SAM's proposal for a point or a rectangle, listed as region ids."""
    from ..vision import sam

    art = imread(Path(args.src))
    if args.point:
        pts = [tuple(float(v) for v in p.split(",")) for p in args.point]
        m = sam.segment(art, points=pts)
    else:
        box = tuple(float(v) for v in args.box.split(","))
        parts = sam.segment_each(art, [box])
        m = parts[0] if parts else None
    if m is None:
        print(json.dumps({"sam": None}))
        return 1
    regs = regions(art, colours=args.colours, min_area=args.min_area)
    hits = [r["id"] for r in regs
            if (r["mask"] & m).sum() > 0.5 * r["area"]]
    print(json.dumps({"sam_area_pct": round(100.0 * float(m.mean()), 2),
                      "regions_mostly_inside": hits}, ensure_ascii=False))
    return 0


def cmd_cut(args) -> int:
    art = imread(Path(args.src))
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    rgba, meta = apply_spec(art, spec)
    out = ensure_dir(Path(args.to))
    png = out / f"{args.name}.png"
    Image.fromarray(rgba, "RGBA").save(png)
    write_json(out / f"{args.name}.json",
               {"kind": meta["kind"], "art": args.src, "art_w": int(art.shape[1]),
                "art_h": int(art.shape[0]), "parts": [{"name": args.name,
                                                      "image": str(png),
                                                      "pivot": meta["pivot"],
                                                      "amp": float(spec.get("amp", 12.0))}]})
    write_json(out / f"{args.name}_meta.json",
               {k: meta[k] for k in ("offset_px", "size_px", "pivot", "regions",
                                     "holes", "how", "label")})
    s = score(rgba, meta, art)
    print(json.dumps({"piece": str(png), "review": s}, ensure_ascii=False))
    return 0 if s["accept"] else 2


def cmd_review(args) -> int:
    rig = json.loads(Path(args.rig).read_text(encoding="utf-8"))
    part = rig["parts"][0]
    rgba = np.asarray(Image.open(Path(part["image"]).replace("\\", "/")).convert("RGBA"))
    frame = imread(Path(rig["art"]))
    meta = {"pivot": part["pivot"]}
    s = score(rgba, meta, frame)
    print(json.dumps(s, ensure_ascii=False))
    return 0 if s["accept"] else 2
