"""Render 劳拉's fixed arm as a looping wave, with the agent's own motion rules.

    python scripts/p17_wave.py

No hand tuning beyond the two contract files:
  * the arm piece comes from `book2real agent rig-arm`
  * the plan is a plain beats JSON, written here and rendered by
    `book2real agent motion`

The pivot is the shoulder end (bottom-left of the cut), so the whole forearm and
hand swing as one rigid piece — that is what "the hand is not cut off" looks
like when it moves.
"""
import json
import sys

sys.path.insert(0, '.')
from pathlib import Path

from PIL import Image

from book2real.motion_video import render, write_plan

OUT = Path("runs/agent/p17")
piece = json.load(open(OUT / "arm_full.json", encoding="utf-8"))["parts"][0]
arm = Image.open(OUT / Path(piece["image"]).name)

# where the arm sits on the page, from the cut's own offset (the frame is the
# whole page, so offset_px is exactly its position)
offset = json.load(open(OUT / "arm_full.json", encoding="utf-8"))
x0, y0 = 0, 0
try:
    meta = json.load(open(OUT / "arm_full_meta.json", encoding="utf-8"))
    x0, y0 = meta["offset_px"]
except Exception:
    pass

page = Image.open("runs/agent/p17/frame_fixed.jpg").convert("RGB")
W, H = page.size

plan = {
    "page": "p17",
    "size": [int(W * 0.62), int(H * 0.62)],
    "bg": "#cf3f2f",
    "fps": 20,
    "seconds": 2.4,
    "layers": [
        {"image": "frame_scaled.jpg", "x": 0, "y": 0},
        {"image": "arm_scaled.png", "x": int(x0 * 0.62), "y": int(y0 * 0.62),
         "pivot": [0.42, 0.97],
         "beats": [{"t": 0.0, "action": "wave", "amp": 12.0, "cycles": 2.0, "dur": 2.4}]},
    ],
}
page.resize((int(W * 0.62), int(H * 0.62)), Image.LANCZOS).save(
    OUT / "frame_scaled.jpg", quality=88)
arm.resize((max(1, int(arm.width * 0.62)), max(1, int(arm.height * 0.62))),
           Image.LANCZOS).save(OUT / "arm_scaled.png")
write_plan(OUT / "p17_wave_plan.json", plan)
meta = render(OUT / "p17_wave_plan.json", OUT / "p17_wave.gif")
print(json.dumps(meta, ensure_ascii=False))
