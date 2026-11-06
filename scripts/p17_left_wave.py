"""p17: swap the fake wave for a real one on the *left* arm.

The right arm overlaps the cheek (uncuttable by boundary operators, measured in
docs/CUTOUT_LIMITS.md). The left arm stands on the red paper with a closed ink
outline, so `book2real agent part` cuts it cleanly — `review` accepts it with
0% paper and one component.

This script renders the swing loop from that piece and the frame behind it.
"""
import json
import sys

sys.path.insert(0, '.')
from pathlib import Path

from PIL import Image

from book2real.motion_video import render, write_plan

OUT = Path("runs/agent/p17")
SCALE = 0.62


def main() -> None:
    piece = json.load(open(OUT / "left_arm.json", encoding="utf-8"))["parts"][0]
    meta = json.load(open(OUT / "left_arm_meta.json", encoding="utf-8"))
    frame = Image.open(OUT / "frame_fixed.jpg").convert("RGB")
    arm = Image.open(OUT / "left_arm.png").convert("RGBA")
    W, H = frame.size
    frame.resize((int(W * SCALE), int(H * SCALE)), Image.LANCZOS).save(
        OUT / "frame_small.jpg", quality=88)
    arm.resize((max(1, int(arm.width * SCALE)), max(1, int(arm.height * SCALE))),
               Image.LANCZOS).save(OUT / "left_arm_small.png")

    # pivot is the bottom end of the forearm (the elbow), which is where the arm
    # leaves the body; the hand is the far tip and swings around it
    plan = {
        "page": "p17",
        "size": [int(W * SCALE), int(H * SCALE)],
        "bg": "#cf3f2f",
        "fps": 20,
        "seconds": 2.4,
        "layers": [
            {"image": "frame_small.jpg", "x": 0, "y": 0},
            {"image": "left_arm_small.png",
             "x": int(meta["offset_px"][0] * SCALE), "y": int(meta["offset_px"][1] * SCALE),
             "pivot": [0.594, 0.98],
             "beats": [{"t": 0.0, "action": "wave", "amp": 14.0,
                        "cycles": 2.0, "dur": 2.4}]},
        ],
    }
    write_plan(OUT / "left_arm_wave_plan.json", plan)
    out = render(OUT / "left_arm_wave_plan.json", OUT / "p17_left_wave.gif")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
