"""Virtual camera: per-frame (zoom, pan) for a shot, plus per-layer parallax."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .contracts import ShotSpec
from .util import ease


@dataclass
class CamPose:
    scale: float = 1.0
    dx: float = 0.0     # fraction of width
    dy: float = 0.0     # fraction of height


def pose(spec: ShotSpec, t: float) -> CamPose:
    """t in [0,1] -> camera pose. `amount` is relative to frame size."""
    cam = spec.camera
    a = float(cam.amount)
    e = ease(t, cam.ease)
    move = cam.move

    if move == "none":
        return CamPose(1.0, 0.0, 0.0)
    if move == "dolly_in":
        return CamPose(1.0 + a * e, 0.0, -0.02 * a * e)
    if move == "dolly_out":
        return CamPose(1.0 + a * (1.0 - e), 0.0, 0.0)
    if move == "pan_lr":
        return CamPose(1.0 + 0.05 * a, a * (e - 0.5), 0.0)
    if move == "pan_rl":
        return CamPose(1.0 + 0.05 * a, -a * (e - 0.5), 0.0)
    if move == "tilt_up":
        return CamPose(1.0 + 0.05 * a, 0.0, -a * (e - 0.5))
    if move == "tilt_down":
        return CamPose(1.0 + 0.05 * a, 0.0, a * (e - 0.5))
    if move == "drift":
        w = 2.0 * math.pi
        return CamPose(1.0 + 0.03 * a, a * 0.45 * math.sin(w * t),
                       a * 0.22 * math.sin(w * t + 1.1))
    # kenburns (default): slow push with a diagonal drift
    return CamPose(1.0 + 0.75 * a * e, a * (e - 0.5) * 0.8, a * (e - 0.5) * 0.35)


def layer_pose(base: CamPose, depth: float, parallax: float) -> CamPose:
    """Near layers translate more and zoom slightly more than far layers."""
    p = depth * parallax
    return CamPose(
        scale=1.0 + (base.scale - 1.0) * (1.0 + 0.30 * p),
        dx=base.dx * (1.0 + 1.25 * p),
        dy=base.dy * (1.0 + 1.25 * p),
    )
