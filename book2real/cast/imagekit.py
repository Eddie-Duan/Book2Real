"""Vision helpers for the cast agents.

numpy + Pillow only; OpenCV is used when it happens to be installed because
exact connected components are much cheaper than a Python BFS. Every helper is
deterministic: same page in, same numbers out.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

try:                                                     # optional, much faster
    import cv2
except Exception:                                        # pragma: no cover
    cv2 = None


@dataclass
class Component:
    area: int
    box: tuple[int, int, int, int]                       # x0, y0, x1, y1 (exclusive)
    mask: np.ndarray                                     # bool, page sized

    @property
    def w(self) -> int:
        return self.box[2] - self.box[0]

    @property
    def h(self) -> int:
        return self.box[3] - self.box[1]


# ---------------------------------------------------------------- colour
def border_key(rgb: np.ndarray, band: float = 0.02) -> tuple[np.ndarray, float]:
    """Median colour of the page border + how uniform that border is."""
    h, w = rgb.shape[:2]
    b = max(2, int(round(min(h, w) * band)))
    px = np.concatenate([
        rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3),
        rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3),
    ]).astype(np.float32)
    key = np.median(px, axis=0)
    spread = float(np.median(np.linalg.norm(px - key, axis=1)))
    return key.astype(np.float32), spread


def colour_distance(rgb: np.ndarray, key: np.ndarray) -> np.ndarray:
    return np.linalg.norm(rgb.astype(np.float32) - np.asarray(key, np.float32), axis=2)


def near_colour(rgb: np.ndarray, key: np.ndarray, tol: float) -> np.ndarray:
    return colour_distance(rgb, key) < float(tol)


# ---------------------------------------------------------------- masks
def _propagate(seed: np.ndarray, allow: np.ndarray, iters: int = 400) -> np.ndarray:
    cur = seed & allow
    for _ in range(iters):
        nxt = cur.copy()
        nxt[1:] |= cur[:-1]
        nxt[:-1] |= cur[1:]
        nxt[:, 1:] |= cur[:, :-1]
        nxt[:, :-1] |= cur[:, 1:]
        nxt &= allow
        if np.array_equal(nxt, cur):
            break
        cur = nxt
    return cur


def _small(mask: np.ndarray, target: int = 240) -> tuple[np.ndarray, float]:
    h, w = mask.shape[:2]
    s = min(1.0, float(target) / max(h, w, 1))
    if s >= 1.0:
        return mask, 1.0
    im = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    small = np.asarray(im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.BOX)) > 90
    return small, s


def _flood_from_border(allow: np.ndarray) -> np.ndarray:
    """Pixels of `allow` reachable from the image border (4-connectivity)."""
    if cv2 is not None:
        m = np.ascontiguousarray(allow.astype(np.uint8))
        h, w = m.shape
        mask = np.zeros((h + 2, w + 2), np.uint8)
        seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
        filled = np.zeros_like(m)
        for sx, sy in seeds:
            if not m[sy, sx] or filled[sy, sx]:
                continue
            tmp = m.copy()
            cv2.floodFill(tmp, mask, (sx, sy), 2)
            filled |= tmp == 2
        return filled.astype(bool)
    seed = np.zeros_like(allow)
    seed[0, :] = seed[-1, :] = True
    seed[:, 0] = seed[:, -1] = True
    return _propagate(seed, allow)


def background_from_border(near: np.ndarray) -> np.ndarray:
    """Pixels of `near` (background-coloured) that are reachable from the border."""
    h, w = near.shape[:2]
    small, s = _small(near)
    grown = _flood_from_border(small)
    if s >= 1.0:
        return grown
    up = np.asarray(Image.fromarray((grown * 255).astype(np.uint8), "L")
                    .resize((w, h), Image.BILINEAR)) > 100
    return np.logical_and(up, near)


def key_alpha(rgb: np.ndarray, key: np.ndarray, tol: float = 60.0,
              blur: float = 0.8) -> tuple[np.ndarray, float]:
    """Alpha for art drawn on a flat colour: 255 = figure, 0 = background.

    `separation` is the median colour distance between figure and key (0..441);
    a low value means the art does not really sit on that flat colour.
    """
    near = near_colour(rgb, key, tol)
    bg = background_from_border(near)
    a = (~bg).astype(np.uint8) * 255
    if cv2 is not None:
        a = cv2.morphologyEx(a, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    dist = colour_distance(rgb, key)
    sep = float(np.median(dist[~bg])) if (~bg).any() else 0.0
    if blur > 0:
        a = np.asarray(Image.fromarray(a, "L").filter(ImageFilter.GaussianBlur(blur)))
    return a, sep


def components(mask: np.ndarray, min_area: float = 0, max_area: float | None = None,
               limit: int = 12) -> list[Component]:
    """Connected components (4-connectivity), largest first."""
    m = np.ascontiguousarray(mask.astype(np.uint8))
    h, w = m.shape[:2]
    total = float(max(1, h * w))
    out: list[Component] = []
    if cv2 is not None:
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 4)
        for i in range(1, n):
            x, y, cw, ch, area = (int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]),
                                  int(stats[i, 3]), int(stats[i, 4]))
            if area < max(1.0, min_area * total):
                continue
            if max_area is not None and area > max_area * total:
                continue
            out.append(Component(area, (x, y, x + cw, y + ch), lab == i))
    else:                                                # pragma: no cover - fallback
        small, s = _small(mask.astype(bool))
        lab_s, count = _label(small)
        for k in range(1, count + 1):
            box_s = _box_of(lab_s == k)
            if box_s is None:
                continue
            x0, y0, x1, y1 = (int(round(v / s)) for v in box_s)
            x1, y1 = min(w, max(x0 + 1, x1)), min(h, max(y0 + 1, y1))
            sub_lab, sub_n = _label(mask[y0:y1, x0:x1])
            if sub_n == 0:
                continue
            sizes = [int((sub_lab == j).sum()) for j in range(1, sub_n + 1)]
            j = 1 + int(np.argmax(sizes))
            area = sizes[j - 1]
            if area < min_area * total or (max_area is not None and area > max_area * total):
                continue
            full = np.zeros_like(mask, bool)
            full[y0:y1, x0:x1] = sub_lab == j
            out.append(Component(area, _box_of(full) or (x0, y0, x1, y1), full))
    out.sort(key=lambda c: c.area, reverse=True)
    return out[:limit]


def _label(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """4-connected labelling for small grids (used only without OpenCV)."""
    from collections import deque

    h, w = mask.shape[:2]
    lab = np.zeros((h, w), np.int32)
    cur = 0
    for y in range(h):
        row = mask[y]
        for x in range(w):
            if row[x] and lab[y, x] == 0:
                cur += 1
                dq = deque([(y, x)])
                lab[y, x] = cur
                while dq:
                    cy, cx = dq.popleft()
                    for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and lab[ny, nx] == 0:
                            lab[ny, nx] = cur
                            dq.append((ny, nx))
    return lab, cur


def _box_of(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


# ---------------------------------------------------------------- features
def colour_signature(rgb: np.ndarray, mask: np.ndarray, bins: int = 4) -> np.ndarray:
    """Normalised RGB histogram of the masked pixels (cheap identity feature)."""
    px = rgb[mask]
    if len(px) < 16:
        return np.zeros(bins ** 3, np.float32)
    q = np.clip((px.astype(np.int32) * bins) // 256, 0, bins - 1)
    idx = (q[:, 0] * bins + q[:, 1]) * bins + q[:, 2]
    hist = np.bincount(idx, minlength=bins ** 3).astype(np.float32)
    return hist / max(1e-6, float(hist.sum()))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return 0.0 if d <= 0 else float(np.dot(a, b) / d)


def pad_to_ratio(img: Image.Image, ratio: float, fill=(250, 247, 240)) -> Image.Image:
    """Pad (never crop) so the image matches `ratio` = w/h."""
    w, h = img.size
    if w / h > ratio:
        nh = int(round(w / ratio))
        canvas = Image.new("RGB", (w, nh), fill)
        canvas.paste(img, (0, (nh - h) // 2))
        return canvas
    nw = int(round(h * ratio))
    canvas = Image.new("RGB", (nw, h), fill)
    canvas.paste(img, ((nw - w) // 2, 0))
    return canvas


def crop_box(rgb: np.ndarray, box: tuple[float, float, float, float], pad: float = 0.06,
             clamp: bool = True) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Crop a normalised xywh box with padding; returns (crop, pixel box)."""
    h, w = rgb.shape[:2]
    x, y, bw, bh = box
    px, py = bw * pad, bh * pad
    x0 = int(round((x - px) * w))
    y0 = int(round((y - py) * h))
    x1 = int(round((x + bw + px) * w))
    y1 = int(round((y + bh + py) * h))
    if clamp:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
    x1 = max(x0 + 2, x1)
    y1 = max(y0 + 2, y1)
    return rgb[y0:y1, x0:x1].copy(), (x0, y0, x1, y1)


def ring(mask: np.ndarray, px: int = 8) -> np.ndarray:
    """Band of roughly `px` pixels just outside the mask."""
    im = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    d = np.asarray(im.filter(ImageFilter.GaussianBlur(max(1.0, px / 2.0)))) > 40
    return np.logical_and(d, ~mask)


def contact_sheet(items: list[tuple[str, np.ndarray, tuple[float, float] | None]],
                  size: tuple[int, int]) -> Image.Image:
    """Debug sheet: each RGBA part over a flat card, pivot marked."""
    w, h = size
    sheet = Image.new("RGB", (w * max(1, len(items)), h), (240, 238, 233))
    for i, (name, rgba, pivot) in enumerate(items):
        im = Image.fromarray(rgba, "RGBA")
        card = Image.new("RGB", (w, h), (236, 233, 227))
        card.paste(im, (0, 0), im)
        d = ImageDraw.Draw(card)
        if pivot:
            px, py = int(pivot[0] * w), int(pivot[1] * h)
            d.ellipse((px - 5, py - 5, px + 5, py + 5), outline=(255, 0, 90), width=2)
        d.text((6, 6), name, fill=(20, 20, 20))
        sheet.paste(card, (i * w, 0))
    return sheet
