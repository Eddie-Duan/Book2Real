"""Build a paper-puppet cutout + plate for a character.

This is the authoring side of the 2.5D rig: the character is cut from the
illustration, the hole behind it is inpainted, and a small handle set (head /
hand / body) is written for the Canvas mesh renderer. The pixels stay the
original book art; only the mesh moves.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book2real import imgops as I  # noqa: E402
from book2real.util import imread, write_json  # noqa: E402


def cutout(art_path: Path, box, out_png: Path, out_plate: Path,
           white: int = 228, feather: float = 1.2,
           fill: tuple | None = None, grabcut: bool = False,
           poly: list | None = None, floor_key: bool = False) -> dict:
    art = imread(art_path)
    h, w = art.shape[:2]
    x, y, cw, ch = box
    x0, y0 = int(x * w), int(y * h)
    x1, y1 = int((x + cw) * w), int((y + ch) * h)
    crop = art[y0:y1, x0:x1].copy()

    # Background = near-white connected to the crop border. White areas inside
    # the character (dress, eyes) are enclosed by ink and therefore stay.
    if grabcut:
        gc = np.zeros(crop.shape[:2], np.uint8)
        bgm = np.zeros((1, 65), np.float64)
        fgm = np.zeros((1, 65), np.float64)
        rect = (2, 2, crop.shape[1] - 4, crop.shape[0] - 4)
        cv2.grabCut(cv2.cvtColor(crop, cv2.COLOR_RGB2BGR), gc, rect, bgm, fgm, 6,
                    cv2.GC_INIT_WITH_RECT)
        char = np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    else:
        near_white = (crop.min(axis=2) > white).astype(np.uint8)
        n, labels = cv2.connectedComponents(near_white, connectivity=4)
        border = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])
        border.discard(0)
        bg = np.isin(labels, list(border))
        char = (~bg).astype(np.uint8) * 255
    char = cv2.morphologyEx(char, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    if poly:
        pm = np.zeros(crop.shape[:2], np.uint8)
        pts = np.array([[int(px * crop.shape[1]), int(py * crop.shape[0])] for px, py in poly], np.int32)
        cv2.fillPoly(pm, [pts], 255)
        char = cv2.bitwise_and(char, pm)
    if floor_key:
        hh, ww = crop.shape[:2]
        yy, xx = np.mgrid[0:hh, 0:ww]
        f = crop.astype(np.int16)
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        sat = (np.max(f, axis=2) - np.min(f, axis=2)) / np.maximum(1, np.max(f, axis=2))
        warm = (r > g + 12) & (g > b + 8) & (sat > 0.18)
        floor = warm & (yy > hh * 0.62)
        char[floor] = 0
    # keep the largest component (drop stray specks)
    n2, lab2, stats, _ = cv2.connectedComponentsWithStats(char, 8)
    if n2 > 1:
        idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        char = ((lab2 == idx) * 255).astype(np.uint8)
    alpha = Image.fromarray(char).filter(ImageFilter.GaussianBlur(feather))
    a = np.asarray(alpha)
    ys, xs = np.where(a > 20)
    cx0, cx1, cy0, cy1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    rgba = np.dstack([crop[:, :, :3], a])[cy0:cy1, cx0:cx1]
    out_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(out_png)

    # plate: erase the character; on flat paper fill with the sampled colour,
    # otherwise Telea (it sits behind the puppet and is mostly occluded)
    alpha_full = np.zeros((h, w), np.uint8)
    alpha_full[y0 + cy0:y0 + cy1, x0 + cx0:x0 + cx1] = a[cy0:cy1, cx0:cx1]
    if fill is None:
        ring = art[max(0, y0 - 12):y0, max(0, x0 - 12):x1, :].reshape(-1, 3)
        med = np.median(ring, axis=0) if len(ring) else np.array([255, 255, 255])
        fill = tuple(int(v) for v in med)
    plate = art.copy()
    plate[alpha_full > 20] = fill
    # soften the seam: fade the fill over the character edge, never put the
    # character back (that is what a naive blur does)
    m = (alpha_full > 20).astype(np.uint8) * 255
    m = cv2.GaussianBlur(m, (5, 5), 1.2)
    wgt = m[..., None].astype(np.float32) / 255.0
    fill_arr = np.array(fill, np.float32).reshape(1, 1, 3)
    plate = (art.astype(np.float32) * (1 - wgt) + fill_arr * wgt).astype(np.uint8)
    # make the outer paper transparent so the Canvas plate sits on the stage
    near_white = (plate.min(axis=2) > 226).astype(np.uint8)
    n3, lab3 = cv2.connectedComponents(near_white, connectivity=4)
    border3 = set(lab3[0, :]) | set(lab3[-1, :]) | set(lab3[:, 0]) | set(lab3[:, -1])
    border3.discard(0)
    outer = np.isin(lab3, list(border3))
    alpha = np.where(outer, 0, 255).astype(np.uint8)
    Image.fromarray(np.dstack([plate, alpha]), "RGBA").save(out_plate)
    return {"x": int(x0 + cx0), "y": int(y0 + cy0), "w": int(cx1 - cx0),
            "h": int(cy1 - cy0), "art_w": int(w), "art_h": int(h)}


def main():
    book = ROOT / "books" / "charlie_lola"
    art = book / "reader" / "art"
    out = book / "reader" / "rig"
    rigs = {}

    m = cutout(art / "p16.jpg", (0.32, 0.12, 0.42, 0.88),
               out / "p16_lola.png", out / "p16_plate.png", fill=(255, 255, 255))
    rigs["p16"] = {
        "plate": "rig/p16_plate.png", "image": "rig/p16_lola.png",
        "box": [m["x"], m["y"], m["w"], m["h"]],
        "art_w": m["art_w"], "art_h": m["art_h"],
        "characters": [{
            "name": "劳拉",
            "handles": [
                {"id": "body", "type": "scale", "pivot": [0.5, 0.7], "radius": 0.7,
                 "amount": 0.012, "freq": 1.6},
                {"id": "head", "type": "rot", "pivot": [0.50, 0.40], "radius": 0.36,
                 "amp": 8.0, "freq": 1.8, "phase": 0.0},
                {"id": "hand", "type": "rot", "pivot": [0.34, 0.42], "radius": 0.16,
                 "amp": 12.0, "freq": 2.6, "phase": 0.7},
                {"id": "hand2", "type": "rot", "pivot": [0.58, 0.47], "radius": 0.14,
                 "amp": 8.0, "freq": 2.2, "phase": 2.0},
            ],
        }],
    }

    write_json(out / "rig.json", rigs)
    print(rigs["p16"]["box"])


if __name__ == "__main__":
    main()
