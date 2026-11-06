"""Recraft first paying call: the cheapest endpoint, to calibrate credits.

    python scripts/recraft_calibrate.py

Runs one eraseRegion on a small synthetic region of p03 ($0.002), then reads the
balance and the `credits` field of the response, so the real credit-to-dollar
ratio is measured instead of assumed. Prints both.
"""
import json
import sys

sys.path.insert(0, '.')
from pathlib import Path

import numpy as np
from PIL import Image

from book2real.vision import recraft

art = Image.open("books/charlie_lola/reader/art/p03.jpg").convert("RGB")
w, h = art.size

before = recraft.users_me()["credits"]
# erase a 40x40 patch of empty sofa (no character there), a harmless test area
mask = Image.new("L", (w, h), 0)
mask.paste(255, (int(0.62 * w), int(0.55 * h), int(0.62 * w) + 40, int(0.55 * h) + 40))

out, body = recraft.erase(art, mask)
after = recraft.users_me()["credits"]
credits = body.get("credits")
print(json.dumps({"balance_before": before, "balance_after": after,
                  "delta": before - after, "response_credits": credits},
                 ensure_ascii=False))
out.save("runs/_recraft_erase.png")
print("saved runs/_recraft_erase.png")
