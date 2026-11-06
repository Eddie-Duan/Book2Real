"""Page-level motion: beats -> a looping GIF or MP4, from cut parts.

    python -m book2real agent motion --plan plan.json --out runs/agent/loop.gif

A plan is plain JSON (no code, no model) so a planning agent can write one and a
different agent can render it, on another machine, without this conversation:

    {"page": "p17", "size": [360, 460], "bg": "#cf3f2f", "fps": 20, "seconds": 2.4,
     "layers": [
        {"image": "frame.png", "x": 0, "y": 0, "w": 360, "h": 460},
        {"image": "arm_00.png", "x": 88, "y": 20, "pivot": [0.85, 0.5],
         "beats": [{"t":0.0,"action":"wave","amp":16,"cycles":2,"dur":2.4}]}
     ]}

Rendering is rigid: every layer is rotated/pasted as one image, never warped.
Sampled layers (`image_00.png`, `image_01.png`, ...) are cross-faded between the
poses, which is how a "wave" made of redrawn poses plays without any bending.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image

from .util import ensure_dir, read_json


def _ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def _angle(beats: list[dict], t: float) -> float:
    """Sum of the rotations each beat asks for at time t (degrees)."""
    total = 0.0
    for b in beats:
        if b.get("action") not in ("wave", "point", "shrug", "nod"):
            continue
        t0 = float(b.get("t", 0.0))
        dur = max(0.15, float(b.get("dur", 1.2)))
        cycles = float(b.get("cycles", 1))
        amp = float(b.get("amp", 14.0))
        if not (t0 <= t <= t0 + dur):
            continue
        if b.get("action") == "nod":
            total += amp * math.sin(2 * math.pi * cycles * (t - t0) / dur)
        else:
            phase = (t - t0) / dur
            total += amp * math.sin(2 * math.pi * cycles * phase) * _ease(min(1.0, phase * 3))
    return total


def write_plan(path: str | Path, plan: dict) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _layer_image(layer: dict, out_dir: Path) -> list[Image.Image]:
    if layer.get("sequence"):
        imgs = []
        for name in layer["sequence"]:
            p = Path(name)
            imgs.append(Image.open(p if p.is_absolute() else out_dir / name).convert("RGBA"))
        return imgs
    p = Path(layer["image"])
    img = Image.open(p if p.is_absolute() else out_dir / p.name).convert("RGBA")
    return [img]


def render(plan_path: str | Path, out_path: str | Path) -> dict:
    plan = read_json(plan_path)
    out_dir = Path(plan_path).resolve().parent
    size = tuple(plan.get("size") or (360, 460))
    bg = plan.get("bg") or "#f6efe2"
    fps = int(plan.get("fps") or 20)
    seconds = float(plan.get("seconds") or 2.4)
    n = max(2, int(round(seconds * fps)))
    layers = []
    for lay in plan.get("layers", []):
        frames = _layer_image(lay, out_dir)
        layers.append((lay, frames))

    out = []
    for i in range(n):
        t = seconds * i / (n - 1)
        canvas = Image.new("RGBA", size, bg)
        for lay, frames in layers:
            if len(frames) == 1:
                img = frames[0]
                if lay.get("pivot"):
                    ang = _angle(lay.get("beats") or [], t)
                    # rotate the layer on its own canvas first, then paste it at
                    # its place: rotating after the paste drags the piece outside
                    # the frame and the lost corners reappear at the opposite edge
                    w, h = img.size
                    diag = int((w * w + h * h) ** 0.5) + 2
                    pad = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
                    pad.alpha_composite(img, ((diag - w) // 2, (diag - h) // 2))
                    px = float(lay["pivot"][0]) * w + (diag - w) / 2
                    py = float(lay["pivot"][1]) * h + (diag - h) / 2
                    rot = pad.rotate(ang, center=(px, py), resample=Image.BICUBIC)
                    canvas.alpha_composite(
                        rot, (int(lay.get("x", 0) - (diag - w) / 2),
                              int(lay.get("y", 0) - (diag - h) / 2)))
                else:
                    canvas.alpha_composite(img, (int(lay.get("x", 0)), int(lay.get("y", 0))))
            else:
                # sampled poses: pick the pair and cross-fade
                phase = t / seconds * (len(frames) - 1)
                k = min(len(frames) - 2, int(phase))
                f = phase - k
                a, b = frames[k], frames[min(len(frames) - 1, k + 1)]
                blended = Image.blend(a, b, _ease(f)) if a.size == b.size else (a if f < 0.5 else b)
                canvas.alpha_composite(blended, (int(lay.get("x", 0)), int(lay.get("y", 0))))
        out.append(canvas.convert("RGB"))

    out_path = Path(out_path)
    ensure_dir(out_path.parent)
    if out_path.suffix.lower() == ".gif":
        out[0].save(out_path, save_all=True, append_images=out[1:],
                    duration=max(20, int(1000 / fps)), loop=0, optimize=True)
    else:
        import numpy as np

        from .encode import encode_frames

        w, h = size
        arrs = (np.asarray(im) for im in out)
        encode_frames(arrs, out_path, fps, (w, h))
    return {"out": str(out_path), "frames": n, "fps": fps, "seconds": seconds,
            "layers": len(layers)}
