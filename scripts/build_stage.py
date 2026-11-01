"""Build clean character portraits + a 3-layer paper puppet for Lola.

The puppet is deliberately *rigid parts*: body, left arm, right arm. The arms
are cut from the original art and rotate around their shoulders. There is no
mesh warping, so the drawing cannot wobble or stretch.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "books" / "charlie_lola"
ART = BOOK / "reader" / "art"
OUT = BOOK / "reader" / "characters"


def key_alpha(art: np.ndarray, key: np.ndarray, tol: int = 55) -> np.ndarray:
    """Foreground = everything not connected to the border via the key colour."""
    near = (np.linalg.norm(art.astype(np.float32) - key, axis=2) < tol).astype(np.uint8)
    n, lab = cv2.connectedComponents(near, connectivity=4)
    border = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    border.discard(0)
    bg = np.isin(lab, list(border))
    a = (~bg).astype(np.uint8) * 255
    a = cv2.morphologyEx(a, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n2, lab2, stats, _ = cv2.connectedComponentsWithStats(a, 8)
    if n2 > 1:
        idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        a = ((lab2 == idx) * 255).astype(np.uint8)
    return a


def make_puppet():
    art = np.asarray(Image.open(ART / "p17.jpg").convert("RGB"))
    h, w = art.shape[:2]
    key = np.median(np.concatenate([art[:20].reshape(-1, 3), art[-20:].reshape(-1, 3)]), axis=0)
    a = key_alpha(art, key, 60)
    a = np.asarray(Image.fromarray(a).filter(ImageFilter.GaussianBlur(0.8)))

    polys = {
        "arm_r": [(0.68, 0.55), (0.75, 0.50), (0.82, 0.55), (0.80, 0.68),
                  (0.75, 0.76), (0.68, 0.73), (0.65, 0.62)],
    }
    masks = {}
    body = art.copy()
    pivots = {"arm_r": (0.72, 0.53)}
    for name, pts in polys.items():
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).polygon([(int(x * w), int(y * h)) for x, y in pts], fill=255)
        m = np.asarray(m.filter(ImageFilter.GaussianBlur(0.6)))
        masks[name] = m
        body[m > 30] = key.astype(np.uint8)
    # keep a small disc of the body at each shoulder so a rotating arm never
    # opens a red hole at the joint
    for name, (px, py) in pivots.items():
        disc = np.zeros((h, w), np.uint8)
        cv2.circle(disc, (int(px * w), int(py * h)), int(0.055 * w), 255, -1)
        body[disc > 0] = art[disc > 0]

    Image.fromarray(body).save(OUT / "p17_body.jpg", quality=94)
    for name, m in masks.items():
        alpha = (a.astype(np.float32) * (m.astype(np.float32) / 255.0)).astype(np.uint8)
        rgba = np.dstack([art, alpha])
        Image.fromarray(rgba, "RGBA").save(OUT / f"p17_{name}.png")
    return {
        "body": "characters/p17_body.jpg",
        "arms": {
            "right": {"image": "characters/p17_arm_r.png", "pivot": [0.72, 0.53], "amp": 18},
        },
        "art_w": w, "art_h": h,
    }


def make_portraits():
    specs = {
        "lola": ("art/p17.jpg", (0.12, 0.08, 0.74, 0.88)),
        "charlie": ("pages/p16.jpg", (0.72, 0.30, 0.27, 0.66)),
        "port": ("art/p04.jpg", (0.58, 0.38, 0.34, 0.24)),
        "lotta": ("art/p05.jpg", (0.02, 0.08, 0.30, 0.78)),
        "mimi": ("art/p06.jpg", (0.00, 0.00, 0.16, 0.50)),
    }
    out = {}
    for cid, (f, box) in specs.items():
        im = Image.open(BOOK / "reader" / f).convert("RGB") if f.startswith("art/") else Image.open(BOOK / f).convert("RGB")
        w, h = im.size
        x, y, cw, ch = box
        crop = im.crop((int(x * w), int(y * h), int((x + cw) * w), int((y + ch) * h)))
        # portraits are shown at 4:5; pad rather than crop a face
        tc = 4 / 5
        cw2, ch2 = crop.size
        if cw2 / ch2 > tc:
            nh = int(cw2 / tc)
            canvas = Image.new("RGB", (cw2, nh), (250, 247, 240))
            canvas.paste(crop, (0, (nh - ch2) // 2))
            crop = canvas
        else:
            nw = int(ch2 * tc)
            canvas = Image.new("RGB", (nw, ch2), (250, 247, 240))
            canvas.paste(crop, ((nw - cw2) // 2, 0))
            crop = canvas
        crop.save(OUT / f"portrait_{cid}.jpg", quality=94)
        out[cid] = f"characters/portrait_{cid}.jpg"
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    puppet = make_puppet()
    portraits = make_portraits()
    data = {"puppet": puppet, "portraits": portraits}
    (OUT / "characters.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in puppets_info(puppet).items()}, ensure_ascii=False))


def puppets_info(p):
    return {"body": p["body"], "art": (p["art_w"], p["art_h"])}


if __name__ == "__main__":
    main()
