"""Generate a synthetic picture book so the pipeline can be exercised end to end.

The art is deliberately flat vector-ish, which is what real picture books look
like — it is also the case where our figure/ground decomposition works best.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 900, 1200

NIGHT = ((26, 38, 66), (58, 78, 112))
DUSK = ((46, 44, 78), (128, 96, 106))
DAWN = ((240, 196, 140), (252, 232, 196))
FOREST = ((22, 34, 30), (44, 62, 52))

FONT_PATHS = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]


def _font(size: int):
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size, index=0)
    return ImageFont.load_default()


def _gradient(top, bottom) -> Image.Image:
    col = Image.new("RGB", (1, H))
    px = col.load()
    for y in range(H):
        k = y / (H - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * k) for i in range(3))
    return col.resize((W, H), Image.BILINEAR)


def _stars(d: ImageDraw.ImageDraw, rng: random.Random, n: int, y_max: int):
    for _ in range(n):
        x, y = rng.randrange(0, W), rng.randrange(0, y_max)
        r = rng.choice([1, 1, 1, 2])
        v = rng.randrange(150, 255)
        d.ellipse((x, y, x + r, y + r), fill=(v, v, v))


def _moon(d, cx, cy, r, glow=True):
    """A plain moon. (Earlier versions drew concentric outlines by mistake —
    the `fill=None` ellipses produced visible rings, so keep it simple.)"""
    if glow:
        for k in range(4, 0, -1):
            rr = r * (1 + k * 0.10)
            d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=None, outline=None)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(252, 246, 214))


def _hill(d, y, amp, color, phase=0.0, step=6):
    pts = []
    for x in range(-20, W + 21, step):
        yy = y + amp * math.sin(x / 210.0 + phase) + amp * 0.4 * math.sin(x / 90.0 + phase * 2)
        pts.append((x, yy))
    pts += [(W + 20, H + 20), (-20, H + 20)]
    d.polygon(pts, fill=color)


def _tree(d, x, base, h, w, color=(28, 46, 38)):
    d.rectangle((x - w * .06, base - h * .28, x + w * .06, base), fill=(42, 34, 28))
    for k in range(3):
        top = base - h + k * h * 0.22
        half = w * (0.52 - k * 0.11)
        d.polygon([(x, top), (x - half, top + h * 0.42), (x + half, top + h * 0.42)], fill=color)


def _rabbit(d, x, y, s, facing=1, coat=(248, 246, 240), line=(120, 112, 104)):
    """A simple sitting rabbit built from ellipses."""
    body = (x - 34 * s, y - 56 * s, x + 34 * s, y + 8 * s)
    head = (x - 24 * s, y - 92 * s, x + 24 * s, y - 44 * s)
    d.ellipse(body, fill=coat, outline=line, width=max(1, int(1.5 * s)))
    # ears
    for dx, tilt in ((-11, -8), (11, 6)):
        ex = x + dx * s
        d.ellipse((ex - 7 * s + tilt * s, y - 138 * s, ex + 7 * s + tilt * s, y - 84 * s),
                  fill=coat, outline=line, width=max(1, int(1.5 * s)))
        d.ellipse((ex - 3.4 * s + tilt * s, y - 130 * s, ex + 3.4 * s + tilt * s, y - 92 * s),
                  fill=(246, 205, 205))
    d.ellipse(head, fill=coat, outline=line, width=max(1, int(1.5 * s)))
    # face
    ey = y - 68 * s
    d.ellipse((x - 12 * s, ey - 3 * s, x - 7 * s, ey + 2 * s), fill=(40, 36, 34))
    d.ellipse((x + 7 * s, ey - 3 * s, x + 12 * s, ey + 2 * s), fill=(40, 36, 34))
    d.ellipse((x - 3 * s, y - 58 * s, x + 3 * s, y - 53 * s), fill=(226, 160, 160))
    wx, wy = x + facing * 22 * s, y - 30 * s
    d.ellipse((wx - 10 * s, wy - 10 * s, wx + 10 * s, wy + 10 * s), fill=coat,
              outline=line, width=max(1, int(1.2 * s)))


def _caption(d, text, y=None, size=34):
    y = H - 132 if y is None else y
    d.rounded_rectangle((60, y, W - 60, y + 96), radius=16,
                        fill=(255, 252, 244, 216), outline=(190, 178, 158, 200), width=2)
    f = _font(size)
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=f) > (W - 180):
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    lines.append(cur)
    for i, line in enumerate(lines[:2]):
        d.text((W // 2, y + 26 + i * 40), line, font=f, fill=(48, 42, 36), anchor="mm")


def _water(d, y0, y1, base=(58, 88, 120)):
    d.rectangle((0, y0, W, y1), fill=base)
    rng = random.Random(3)
    for i in range(26):
        yy = y0 + rng.randrange(0, y1 - y0)
        x = rng.randrange(-100, W)
        l = rng.randrange(40, 180)
        d.line((x, yy, x + l, yy), fill=(120, 156, 190), width=rng.choice([1, 2]))


def build(out: str | Path = "books", pages: int = 8) -> Path:
    out = Path(out)
    book_dir = out / "moon_rabbit"
    pages_dir = book_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(2026)

    story = [
        ("夜晚，小兔子推开窗户，发现月亮不见了。", True, "dolly_in"),
        ("他背上小背包，走进了安静的森林。", False, "pan_lr"),
        ("树上的猫头鹰眨眨眼睛：月亮往山那边去了。", True, "dolly_out"),
        ("小溪里漂着亮晶晶的碎片，像碎掉的月亮。", False, "kenburns"),
        ("他爬上山顶，云很厚，风把云吹得很快。", False, "tilt_up"),
        ("云散开了——月亮一直挂在那里，从来没有消失。", True, "dolly_in"),
        ("小兔子躺在草地上，觉得月亮离他很近。", True, "kenburns"),
        ("晚安，月亮。晚安，小兔子。", False, "dolly_out"),
    ]
    story = story[:pages]

    for i, (text, hero, move) in enumerate(story, start=1):
        night = i <= 6
        top, bottom = (NIGHT if night else DUSK) if i != 6 else DAWN
        img = _gradient(top, bottom).convert("RGBA")
        d = ImageDraw.Draw(img, "RGBA")

        if night:
            _stars(d, rng, 120, 780)

        if i <= 5:
            _hill(d, 880, 46, (40, 58, 62), 0.4)
            _hill(d, 960, 30, (30, 46, 50), 1.9)
        else:
            _hill(d, 900, 40, (74, 92, 74), 0.9)
            _hill(d, 990, 26, (58, 74, 60), 2.2)

        if 2 <= i <= 4:
            for tx in (120, 240, 690, 800):
                _tree(d, tx, 980, 300, 150, (26, 42, 36))

        if i <= 5:
            _moon(d, 690, 230, 62)
        if i == 6:
            _moon(d, 660, 210, 96)
        if i in (7, 8):
            _moon(d, 700, 200, 58)

        if i == 4:
            _water(d, 860, 1030, (56, 90, 130))
        if i == 3:
            # owl on a branch
            ox, oy = 250, 560
            d.rounded_rectangle((ox - 4, oy + 40, W - 80, oy + 56), radius=8, fill=(46, 38, 32))
            d.ellipse((ox - 46, oy - 34, ox + 46, oy + 50), fill=(120, 104, 88))
            d.ellipse((ox - 30, oy - 22, ox - 8, oy + 2), fill=(250, 246, 214))
            d.ellipse((ox + 8, oy - 22, ox + 30, oy + 2), fill=(250, 246, 214))
            d.ellipse((ox - 22, oy - 14, ox - 16, oy - 8), fill=(30, 26, 24))
            d.ellipse((ox + 16, oy - 14, ox + 22, oy - 8), fill=(30, 26, 24))
            d.polygon([(ox - 8, oy + 8), (ox + 8, oy + 8), (ox, oy + 20)], fill=(214, 150, 60))

        if i in (1, 2, 5, 6, 7, 8):
            _rabbit(d, 300 if i % 2 else 420, 900, 1.5 if i in (1, 6) else 1.2)
        if i == 4:
            _rabbit(d, 640, 880, 1.1)
        if i == 3:
            _rabbit(d, 620, 960, 1.4)

        _caption(d, text)
        img = img.convert("RGB").filter(ImageFilter.SMOOTH)
        img.save(pages_dir / f"p{i:02d}.png", optimize=True)

    meta = {
        "book_id": "moon_rabbit",
        "title": "小兔子找月亮",
        "subtitle": "Book2Real · 纸书成片",
        "fps": 24,
        "pages": [
            {"file": f"p{i:02d}.png", "text": t, "hero": hero, "move": mv}
            for i, (t, hero, mv) in enumerate(story, start=1)
        ],
    }
    (book_dir / "book.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return book_dir


if __name__ == "__main__":
    print(build())
