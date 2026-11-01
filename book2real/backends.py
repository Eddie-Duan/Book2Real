"""Generation backends.

`parallax`   (A track) layered 2.5D camera move — deterministic, CPU only.
`procedural` (B track) single-plate micro-motion: light sweep, dust, breathe,
             slow drift. Runs on CPU; no model, no network.
`pregen`     (B track) drop-in I2V clips produced elsewhere (Wan / Kling / …),
             matched by shot id — this is the seam for a real generative model.
`static`     last-resort fallback: the page with no camera move.

A backend either yields frames (`produces="frames"`) or returns a clip path
(`produces="video"`). The pipeline handles both; everything downstream only
sees the encoded clip.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np

from . import imgops as I
from .camera import pose
from .config import FilmConfig
from .contracts import ShotSpec
from .render import PageAssets, compose_frame, render_card, render_frames


@dataclass
class Caps:
    id: str
    label: str
    produces: str          # "frames" | "video"
    cost: str              # "cpu" | "gpu" | "network" | "none"
    note: str = ""


@dataclass
class ShotRender:
    frames: Iterator[np.ndarray] | None = None
    video_path: Path | None = None
    info: dict = field(default_factory=dict)


class Backend:
    caps: Caps = Caps("base", "base", "frames", "cpu")

    def available(self) -> tuple[bool, str]:
        return True, ""

    def render(self, spec: ShotSpec, assets: PageAssets, page_rgb: np.ndarray,
               cfg: FilmConfig, src_dir: Path | None = None) -> ShotRender:
        raise NotImplementedError


# --------------------------------------------------------------------- A
class ParallaxBackend(Backend):
    caps = Caps("parallax", "A · 分层视差", "frames", "cpu",
                "分层 + 虚拟相机，确定性输出")

    def render(self, spec, assets, page_rgb, cfg, src_dir=None) -> ShotRender:
        layers = getattr(assets, "layers", None)
        return ShotRender(frames=render_frames(spec, assets, cfg),
                          info={"layers": layers.count if layers else 0})


# --------------------------------------------------------------------- B
class ProceduralBackend(Backend):
    """Micro-motion over the page card: moving light, drifting motes, breath."""

    caps = Caps("procedural", "B · 程序化微动", "frames", "cpu",
                "光影/粒子/呼吸，CPU 合成，无模型")

    def render(self, spec, assets, page_rgb, cfg, src_dir=None) -> ShotRender:
        m = spec.motion or {}
        light = float(m.get("light", 0.55))
        dust = float(m.get("dust", 0.35))
        breathe = float(m.get("breathe", 0.5))
        return ShotRender(frames=self._frames(spec, assets, cfg, light, dust, breathe),
                          info={"light": light, "dust": dust, "breathe": breathe})

    def _frames(self, spec, assets, cfg, light, dust, breathe) -> Iterator[np.ndarray]:
        n = spec.frames
        W, H = cfg.width, cfg.height
        rng = np.random.default_rng(spec.seed or 7)
        parts = [(rng.uniform(0, W), rng.uniform(0, H), rng.uniform(1.5, 4.0),
                  rng.uniform(0.15, 0.55), rng.uniform(0, 6.28)) for _ in range(24)]
        ang = math.radians(-28)
        xs = np.arange(W, dtype=np.float32)[None, :]
        ys = np.arange(H, dtype=np.float32)[:, None]
        proj = (xs * math.cos(ang) + ys * math.sin(ang)) / W
        warm = np.array([1.0, 0.90, 0.72], np.float32)      # warm sunlight, RGB

        for i in range(n):
            t = 0.0 if n == 1 else i / (n - 1)
            card = render_card(spec, assets, t)
            frame = compose_frame(card, assets, _pose_scaled(spec, t, breathe), cfg)

            if light > 0:
                centre = -0.15 + 1.3 * (t * 0.9 + 0.05)
                band = np.exp(-((proj - centre) ** 2) / (2 * 0.085 ** 2))
                g = I.to_gray(frame)
                g = I.blur_f(g * 255.0, 6.0) / 255.0
                add = (band * g * light * 52.0)[..., None] * warm
                frame = np.clip(frame.astype(np.float32) + add, 0, 255).astype(np.uint8)

            if dust > 0:
                ov = np.zeros((H, W), np.float32)
                for (px, py, r, spd, ph) in parts:
                    cx = (px + 78.0 * spd * t + 6 * math.sin(ph + 4 * t)) % (W + 40) - 20
                    cy = (py - 60.0 * spd * t + 5 * math.cos(ph * 1.7 + 3 * t)) % (H + 40) - 20
                    _add_dot(ov, cx, cy, r, 1.0)
                frame = np.clip(frame.astype(np.float32)
                                + ov[..., None] * dust * 130.0, 0, 255).astype(np.uint8)

            yield frame


def _add_dot(buf: np.ndarray, cx: float, cy: float, r: float, val: float) -> None:
    H, W = buf.shape
    reach = max(2.0, r * 2.6)
    x0 = max(0, int(cx - reach))
    x1 = min(W, int(cx + reach) + 1)
    y0 = max(0, int(cy - reach))
    y1 = min(H, int(cy + reach) + 1)
    if x0 >= x1 or y0 >= y1:
        return
    gx = np.exp(-((np.arange(x0, x1) - cx) ** 2) / (2 * r * r))
    gy = np.exp(-((np.arange(y0, y1) - cy) ** 2) / (2 * r * r))
    patch = np.outer(gy, gx) * val
    buf[y0:y1, x0:x1] += patch.reshape(buf[y0:y1, x0:x1].shape)


def _pose_scaled(spec: ShotSpec, t: float, breathe: float = 0.0):
    p = pose(spec, t)
    if breathe > 0:
        p.scale *= 1.0 + 0.010 * breathe * math.sin(2 * math.pi * 0.55 * t)
    return p


# --------------------------------------------------------------------- pregen
class PregenBackend(Backend):
    """Uses an externally generated clip: <book>/clips/<shot_id>.mp4."""

    caps = Caps("pregen", "B · 外部 I2V 片段", "video", "gpu",
                "把 Wan/可灵/Seedance 生成的片段按 shot_id 放进 clips/ 即可")

    def render(self, spec, assets, page_rgb, cfg, src_dir=None) -> ShotRender:
        if src_dir is None:
            raise RuntimeError("pregen backend needs the book directory")
        p = Path(src_dir) / "clips" / f"{spec.shot_id}.mp4"
        if not p.exists():
            raise FileNotFoundError(f"missing pre-generated clip: {p}")
        return ShotRender(video_path=p, info={"source": str(p)})


# --------------------------------------------------------------------- static
class StaticBackend(Backend):
    caps = Caps("static", "兜底 · 静态页", "frames", "none", "任何异常时的最后一种画面")

    def render(self, spec, assets, page_rgb, cfg, src_dir=None) -> ShotRender:
        s = ShotSpec.from_dict(spec.to_dict())
        s.camera.move = "none"
        s.camera.amount = 0.0
        return ShotRender(frames=render_frames(s, assets, cfg), info={})


REGISTRY: dict[str, Backend] = {
    "parallax": ParallaxBackend(),
    "procedural": ProceduralBackend(),
    "pregen": PregenBackend(),
    "static": StaticBackend(),
}

# Degradation policy: what to try when a backend fails or QC rejects a shot.
FALLBACK_CHAIN = ["procedural", "pregen", "parallax", "static"]
