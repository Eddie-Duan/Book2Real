"""Recraft inpaint on the two pages that still show a seam.

    python scripts/recraft_inpaint_test.py         # dry run (no spend)
    python scripts/recraft_inpaint_test.py --go    # two calls, $0.08 total

For each target it builds exactly the mask the layer builder would fill (the
dilated figure cut), asks Recraft to regenerate only that region from a style
prompt derived from the page, and writes:
    runs/recraft/<page>_mask.png
    runs/recraft/<page>_before.jpg    (OpenCV-filled, current pipeline)
    runs/recraft/<page>_after.jpg     (Recraft-filled)
    runs/recraft/<page>_cmp.jpg       (before | after side by side)
"""
import argparse
import sys

sys.path.insert(0, '.')
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from book2real.util import read_json
from book2real.vision import recraft
from book2real.vision import layers as L

BOOK = Path("books/charlie_lola")
READER = BOOK / "reader"
OUT = Path("runs/recraft")

STYLE = {
    "p03": "flat children's picture-book illustration, white sofa, pink paisley "
           "wallpaper, pale blue background, clean black ink outlines, no text",
    "p17": "flat children's picture-book illustration, solid bright red background, "
           "clean black ink outlines, no text, no objects",
}


def target_boxes(page_id: str) -> list[tuple]:
    """The same figure boxes the layer builder uses."""
    lay = L  # keep the import used
    ents = read_json(READER / "cast" / "entities.json")
    cur = read_json(BOOK / "curation.json")
    boxes = []
    for ch in cur.get("characters", []):
        crop = ch.get("crop") or {}
        if crop.get("page") == page_id and crop.get("box"):
            boxes.append(tuple(float(v) for v in crop["box"]))
    page = read_json(READER / "layers" / f"{page_id}.json")
    return boxes, page


def build_mask(art: np.ndarray, boxes: list[tuple]) -> np.ndarray:
    h, w = art.shape[:2]
    keep = np.zeros((h, w), bool)
    for x, y, bw, bh in boxes:
        keep[int(max(0, (y - 0.02) * h)):int(min(h, (y + bh + 0.02) * h)),
             int(max(0, (x - 0.02) * w)):int(min(w, (x + bw + 0.02) * w))] = True
    rim = max(2, int(0.010 * max(h, w)))
    return L._dilate(keep, rim)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="actually spend money")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    for page_id, prompt in STYLE.items():
        art = np.asarray(Image.open(READER / "art" / f"{page_id}.jpg").convert("RGB"))
        boxes, _ = target_boxes(page_id)
        if not boxes:
            print(f"{page_id}: no figure boxes, skipped")
            continue
        mask = build_mask(art, boxes)
        Image.fromarray((mask * 255).astype(np.uint8), "L").save(OUT / f"{page_id}_mask.png")
        base = Image.fromarray(art)
        before, _ = L.fill_background(art, mask, mask)
        Image.fromarray(before).save(OUT / f"{page_id}_before.jpg", quality=93)
        print(f"{page_id}: mask {int(mask.sum())} px, boxes {len(boxes)}")
        if not args.go:
            continue
        imgs, body = recraft.inpaint(base, Image.fromarray((mask * 255).astype(np.uint8), "L"),
                                     prompt, n=1)
        after = imgs[0].convert("RGB")
        after.save(OUT / f"{page_id}_after.jpg", quality=93)
        w, h = before.size
        sh = Image.new("RGB", (w, h * 2 + 10), "white")
        sh.paste(before, (0, 0))
        sh.paste(after, (0, h + 10))
        sh.save(OUT / f"{page_id}_cmp.jpg", quality=90)
        print(f"{page_id}: recraft {body.get('credits')} credits, saved {page_id}_cmp.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
