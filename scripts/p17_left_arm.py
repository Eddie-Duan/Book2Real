"""Cut the *left* arm of 劳拉 on p17 — it stands apart on the red paper.

The right arm overlaps the cheek, which is why every mask attempt merged it into
the face. The left arm is drawn against the paper and already has a closed ink
outline, so the boundary-first pipeline can cut it without any help:

    python scripts/p17_left_arm.py

Steps, all through `book2real agent`:
  1. probe the frame to find the arm's colour region
  2. part with seeds inside that arm
  3. review the piece (machine acceptance)
  4. render a swing loop from the piece
"""
import json
import subprocess
import sys

sys.path.insert(0, '.')
from pathlib import Path

from PIL import Image

OUT = Path("runs/agent/p17")
FRAME = OUT / "frame_fixed.jpg"
PY = sys.executable


def run(*args: str) -> dict:
    r = subprocess.run([PY, "-m", "book2real", "agent", *args],
                       capture_output=True, text=True, encoding="utf-8")
    line = (r.stdout or "").strip().splitlines()
    for ln in reversed(line):
        if ln.startswith("{"):
            try:
                return json.loads(ln)
            except Exception:
                continue
    print("FAILED", args, r.stdout[-500:], r.stderr[-500:])
    return {}


def main() -> int:
    # 1) the arm region: light skin colour, on the red paper, left of the face
    regs = run("probe", "--from", str(FRAME), "--colours", "10", "--min-area", "200",
               "--limit", "40", "--json", str(OUT / "regions_arm.json"))
    # 2) seeds: palm and forearm of the left arm
    piece = run("part", "--from", str(FRAME), "--at", "0.215,0.760",
                "--at", "0.235,0.860", "--to", str(OUT), "--name", "arm_left")
    print("part ->", json.dumps(piece, ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
