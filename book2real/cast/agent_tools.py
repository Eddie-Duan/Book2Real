"""One Rigger step: cut a frame out of a page and return it as a rigid puppet.

    python -m book2real agent rig-gap --from books/.../art/p17.jpg \\
        --to runs/agent/out --cx 0.55 --cy 0.72 --r 0.18 [--pivot 0.52,0.55]

    python -m book2real agent rig-arm --from books/.../art/frame.jpg \\
        --to runs/agent/out --bbox 0.1,0.02,0.30,0.42 [--step 0.12] [--cycles 2]

Why this exists
---------------
The cast chain looks at the whole book and guesses. A person (or later a planning
model) that has already decided "this frame, this rectangle is the arm" should
not have to re-implement the cutting maths, the joint cap, the pivot and the
sampling. These commands are that step, and nothing else: no naming, no prose,
no model calls, so they run the same outside this environment.

Output contract (written to <to>/<name>.json plus the PNGs):

    { "kind": "one_arm|head_nod|paper_puppet",
      "art": "<to>/<name>_body.jpg", "art_w": 240, "art_h": 300, "key": [r,g,b],
      "parts": [{"name": "arm_r", "image": "<to>/<name>_arm_r.png",
                 "pivot": [0.85, 0.10], "amp": 16.0}],
      "plan": {"url": "<to>/<name>_plan.json",
               "frames": 8,
               "beats": [{"t":0.0,"action":"wave","side":"right","amp":16,"cycles":2,"dur":2.4}]},
      "notes": [...] }

`plan` is written by `book2real.motion_video.write_plan` and rendered by
`book2real.motion_video.render` into a GIF/MP4.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ..util import ensure_dir, imread, write_json


def _border_key(rgb: np.ndarray, band: float = 0.02) -> np.ndarray:
    h, w = rgb.shape[:2]
    b = max(2, int(round(min(h, w) * band)))
    px = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3),
                         rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3)]).astype(np.float32)
    return np.median(px, axis=0)


def _key_alpha(rgb: np.ndarray, key: np.ndarray, tol: float = 60.0) -> np.ndarray:
    dist = np.linalg.norm(rgb.astype(np.float32) - key, axis=2)
    near = (dist < tol).astype(np.uint8)
    try:
        import cv2

        n, lab = cv2.connectedComponents(near, connectivity=4)
        border = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
        border.discard(0)
        bg = np.isin(lab, list(border))
    except Exception:                                  # noqa: BLE001
        bg = near.astype(bool)
    return (~bg).astype(np.uint8) * 255


def _disk(shape, cx: int, cy: int, r: int) -> np.ndarray:
    yy, xx = np.ogrid[:shape[0], :shape[1]]
    return ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r


def cut_gap(art: np.ndarray, cx: float, cy: float, r: float, *, pivot: tuple | None = None,
            name: str = "part", out_dir: Path | None = None) -> dict:
    """A round region around (cx, cy) and its surroundings, as a rigid piece.

    The region keeps a ring of the plate around it so the piece overlaps whatever
    it is pasted onto; the rest of the plate is erased with the border colour so
    the piece can be re-pasted at a new rotation without showing a square.
    """
    h, w = art.shape[:2]
    key = _border_key(art)
    alpha_full = _key_alpha(art, key)
    cx_i, cy_i = int(cx * w), int(cy * h)
    r_i = max(6, int(r * min(w, h)))
    piece = _disk((h, w), cx_i, cy_i, r_i)
    piece = np.asarray(Image.fromarray((piece * 255).astype(np.uint8), "L")
                       .filter(ImageFilter.GaussianBlur(1.2)), np.float32) / 255.0
    mask = ((alpha_full.astype(np.float32) / 255.0) * piece)
    rgba = np.dstack([art, (np.clip(mask, 0, 1) * 255).astype(np.uint8)])

    ys, xs = np.where(mask > 0.2)
    if not len(xs):
        raise ValueError("the gap region contains no artwork; check cx, cy")
    m = 6
    x0, y0 = max(0, int(xs.min()) - m), max(0, int(ys.min()) - m)
    x1, y1 = min(w, int(xs.max()) + 1 + m), min(h, int(ys.max()) + 1 + m)
    crop = np.ascontiguousarray(rgba[y0:y1, x0:x1])
    if pivot is None:
        # pivot in the piece's own pixels: the colour-weighted centroid
        a = crop[..., 3].astype(np.float32)
        py, px = np.mgrid[0:crop.shape[0], 0:crop.shape[1]]
        px_c = float((a * px).sum() / max(1.0, a.sum())) / crop.shape[1]
        py_c = float((a * py).sum() / max(1.0, a.sum())) / crop.shape[0]
    else:
        px_c = float(pivot[0])
        py_c = float(pivot[1])
    out_dir = ensure_dir(out_dir or Path("."))
    img_path = out_dir / f"{name}.png"
    Image.fromarray(crop, "RGBA").save(img_path)
    return {"name": name, "image": str(img_path), "pivot": [round(px_c, 4), round(py_c, 4)],
            "amp": 14.0, "offset_px": [x0, y0],
            "box_px": [x0, y0, x1 - x0, y1 - y0]}


def cut_arm(art: np.ndarray, bbox: tuple, *, name: str = "arm_r", out_dir: Path | None = None,
            step: float = 0.0) -> dict:
    """Cut a rigid limb from a frame by rectangle, with a shoulder pivot.

    `step` (normalised page width) adds a sampled sequence: the limb is re-cut at
    x + k*step for k = 0..N, so a wave can be *sampled*, not warped. The first
    frame is the neutral pose.
    """
    h, w = art.shape[:2]
    x, y, bw, bh = (float(v) for v in bbox)
    x0, y0 = int(x * w), int(y * h)
    x1, y1 = int(min(w, (x + bw) * w)), int(min(h, (y + bh) * h))
    key = _border_key(art)
    alpha = _key_alpha(art, key).astype(np.float32) / 255.0
    out_dir = ensure_dir(out_dir or Path("."))

    # pivot: the inner end of the limb, middle height (where it meets the body)
    pivot = (1.0 if "r" in name else 0.0, 0.5)
    frames = [0.0]
    if step:
        n = max(4, min(12, int(round(0.12 / abs(step))) + 1))
        frames = [k * step for k in range(n)]
    plan = []
    neutral = None
    for k, dx in enumerate(frames):
        sx0 = int(np.clip(x0 + dx * w, 0, w - 2))
        sx1 = int(np.clip(x1 + dx * w, sx0 + 2, w))
        plane = np.zeros((h, w), np.float32)
        plane[y0:y1, sx0:sx1] = alpha[y0:y1, sx0:sx1]
        rgba = np.dstack([art, (np.clip(plane, 0, 1) * 255).astype(np.uint8)])
        ys, xs = np.where(plane > 0.3)
        if not len(xs):
            continue
        m = 4
        bx0, by0 = max(0, int(xs.min()) - m), max(0, int(ys.min()) - m)
        bx1, by1 = min(w, int(xs.max()) + 1 + m), min(h, int(ys.max()) + 1 + m)
        crop = np.ascontiguousarray(rgba[by0:by1, bx0:bx1])
        p = out_dir / f"{name}_{k:02d}.png"
        Image.fromarray(crop, "RGBA").save(p)
        plan.append({"image": str(p), "offset_px": [bx0, by0]})
        if k == 0:
            neutral = {"image": str(out_dir / f"{name}.png"), "offset_px": [bx0, by0],
                       "pivot": list(pivot), "amp": 16.0}
            Image.fromarray(crop, "RGBA").save(out_dir / f"{name}.png")
    if neutral is None:
        raise ValueError("the arm rectangle is empty; check --bbox")
    # the last frame is the most raised pose: that is the wave amplitude
    if len(plan) > 1:
        neutral["amp"] = round(8.0 + 8.0 * (len(plan) - 1), 1)
    return neutral


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent rig")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("rig-gap", help="cut a round region around a limb gap")
    g.add_argument("--from", dest="src", required=True)
    g.add_argument("--to", dest="out", required=True)
    g.add_argument("--cx", type=float, required=True)
    g.add_argument("--cy", type=float, required=True)
    g.add_argument("--r", type=float, required=True)
    g.add_argument("--pivot", help="x,y inside the piece")
    g.add_argument("--name", default="part")

    a = sub.add_parser("rig-arm", help="cut a rigid limb by rectangle")
    a.add_argument("--from", dest="src", required=True)
    a.add_argument("--to", dest="out", required=True)
    a.add_argument("--bbox", required=True, help="x,y,w,h normalised")
    a.add_argument("--step", type=float, default=0.0)
    a.add_argument("--name", default="arm_r")

    args = ap.parse_args(argv)
    src = Path(args.src)
    art = imread(src)
    out = ensure_dir(Path(args.out))
    if args.cmd == "rig-gap":
        pivot = None
        if args.pivot:
            p = [float(v) for v in args.pivot.split(",")]
            pivot = (p[0], p[1])
        part = cut_gap(art, args.cx, args.cy, args.r, pivot=pivot, name=args.name, out_dir=out)
        result = {"kind": "head_nod", "art": str(src), "art_w": int(art.shape[1]),
                  "art_h": int(art.shape[0]), "key": [int(v) for v in _border_key(art)],
                  "parts": [{k: part[k] for k in ("name", "image", "pivot", "amp")}]}
    else:
        bbox = tuple(float(v) for v in args.bbox.split(","))
        part = cut_arm(art, bbox, name=args.name, out_dir=out, step=args.step)
        result = {"kind": "one_arm", "art": str(src), "art_w": int(art.shape[1]),
                  "art_h": int(art.shape[0]), "key": [int(v) for v in _border_key(art)],
                  "parts": [{k: part[k] for k in ("name", "image", "pivot", "amp")}]}
    path = out / f"{args.name}.json"
    write_json(path, result)
    print(json.dumps({"wrote": str(path), "kind": result["kind"],
                      "part": result["parts"][0]["image"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
