"""Film rendering: page layers -> camera move -> framed 720p frame.

Two framing modes:
  card : the page sits as a physical card on a blurred, slowly drifting
         backdrop — the "book being filmed" look, and the safe default for
         portrait picture books inside a 16:9 film.
  fill : the page is cropped to the frame and the camera moves inside it —
         for wide spreads, or when a shot should feel like a cut-away.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import imgops as I
from .camera import CamPose, layer_pose, pose
from .config import FilmConfig
from .contracts import ShotSpec
from .layers import Layer, PageLayers, decompose


@dataclass
class PadLayer:
    rgb: np.ndarray
    alpha: np.ndarray     # uint8 0..255
    fit: float
    depth: float


@dataclass
class PageAssets:
    layers: PageLayers
    padded: list                       # list[PadLayer]
    backdrop: np.ndarray
    card_w: int
    card_h: int
    card_mask: np.ndarray              # float32 (ch, cw, 1)
    shadow: np.ndarray                 # float32 (H, W) 0..1 darkness
    aspect: float
    pad: int = 0
    shadow_mul: np.ndarray | None = None   # (H, W, 1) float32, pre-multiplied


def _resize(a: np.ndarray, w: int, h: int, down: bool = True) -> np.ndarray:
    return I.resize(a, w, h, down=down)


def _rounded_mask(w: int, h: int, radius: int) -> np.ndarray:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(0.8))
    return (np.asarray(m, dtype=np.float32) / 255.0)[..., None]


def _backdrop(page: np.ndarray, w: int, h: int, sigma: float, dim: float) -> np.ndarray:
    """Cover-crop then blur at low resolution (a 70px blur at full res is slow)."""
    cov = I.cover(page, w, h)
    small = I.resize(cov, max(8, w // 4), max(8, h // 4))
    small = I.blur(small, max(1.5, sigma / 4.0))
    back = I.resize(small, w, h, down=False)
    return np.clip(back.astype(np.float32) * (1.0 - dim), 0, 255).astype(np.uint8)


def prepare(page_rgb: np.ndarray, cfg: FilmConfig, *, work: int = 760,
            layers: bool = True, fill: bool = False) -> PageAssets:
    if layers:
        page_layers = decompose(page_rgb, work=work)
    else:
        # Flat plate: printed pages with text must stay on one rigid plane.
        # Moving a "text layer" against its background produces ghost glyphs,
        # so the caller can opt out of decomposition per book/page.
        # A full-frame `fill` shot needs a plate at least as wide as the film.
        if fill:
            work = max(work, cfg.width)
        h, w = page_rgb.shape[:2]
        scale = min(1.0, work / max(h, w))
        img = I.resize(page_rgb, int(round(w * scale)), int(round(h * scale))) \
            if scale < 1.0 else page_rgb.copy()
        page_layers = PageLayers(
            [Layer(img, np.ones(img.shape[:2], np.float32), 0.0)],
            np.zeros(img.shape[:2], np.float32), img.shape[:2])
    W, H = cfg.width, cfg.height
    ph, pw = page_rgb.shape[:2]
    aspect = pw / ph

    ch = int(round(H * cfg.card_ratio)) // 2 * 2
    cw = int(round(ch * aspect)) // 2 * 2
    if cw > W * 0.74:
        cw = int(W * 0.74) // 2 * 2
        ch = int(round(cw / aspect)) // 2 * 2

    pad = int(W * 0.06)
    back = _backdrop(page_rgb, W + 2 * pad, H + 2 * pad,
                     cfg.backdrop_blur * W, cfg.backdrop_dim)

    mask = _rounded_mask(cw, ch, max(4, cw // 70))

    x0, y0 = (W - cw) // 2, (H - ch) // 2
    sp = max(8, min(cw // 16, max(8, min(x0, y0) - 2)))
    sh = Image.new("L", (cw + 2 * sp, ch + 2 * sp), 0)
    ImageDraw.Draw(sh).rectangle((sp, sp, sp + cw, sp + ch), fill=255)
    sh = sh.filter(ImageFilter.GaussianBlur(max(2.0, sp / 1.7)))
    shadow = np.zeros((H, W), np.float32)
    patch = np.asarray(sh, np.float32) / 255.0 * cfg.shadow
    sy, sx = y0 - sp, x0 - sp
    y1, x1 = sy + patch.shape[0], sx + patch.shape[1]
    shadow[max(0, sy):min(H, y1), max(0, sx):min(W, x1)] = \
        patch[max(0, -sy):patch.shape[0] - max(0, y1 - H),
              max(0, -sx):patch.shape[1] - max(0, x1 - W)]

    src_h = page_layers.size[0]
    epad = int(round(0.22 * max(page_layers.size))) + 6
    padded = []
    for l in page_layers.layers:
        fit = max(cw / l.rgb.shape[1], ch / l.rgb.shape[0])
        padded.append(PadLayer(
            rgb=I.pad_edge(l.rgb, epad),
            alpha=I.pad_edge((np.clip(l.alpha, 0, 1) * 255).astype(np.uint8), epad),
            fit=fit, depth=l.depth))

    return PageAssets(layers=page_layers, padded=padded, backdrop=back,
                      card_w=cw, card_h=ch, card_mask=mask, shadow=shadow,
                      shadow_mul=(1.0 - shadow)[..., None].astype(np.float32),
                      aspect=aspect, pad=epad)


def _stack(spec: ShotSpec, assets: PageAssets, t: float, w: int, h: int) -> np.ndarray:
    p = pose(spec, t)
    acc = np.zeros((h, w, 3), np.float32)
    for pl in assets.padded:
        # `fit` depends on the target raster: a card-sized buffer and a
        # full-frame `fill` buffer need different cover scales.
        lw = pl.rgb.shape[1] - 2 * assets.pad
        lh = pl.rgb.shape[0] - 2 * assets.pad
        fit = max(w / lw, h / lh)
        lp = layer_pose(p, pl.depth, spec.camera.parallax)
        img = I.warp_padded(pl.rgb, assets.pad, lp.scale, lp.dx, lp.dy, w, h, fit=fit)
        a = I.warp_padded(pl.alpha, assets.pad, lp.scale, lp.dx, lp.dy, w, h, fit=fit)
        a3 = (a.astype(np.float32) / 255.0)[..., None]
        acc = acc * (1.0 - a3) + img.astype(np.float32) * a3
    return np.clip(acc, 0, 255).astype(np.uint8)


def render_card(spec: ShotSpec, assets: PageAssets, t: float) -> np.ndarray:
    return _stack(spec, assets, t, assets.card_w, assets.card_h)


def compose_frame(card: np.ndarray, assets: PageAssets, p: CamPose,
                  cfg: FilmConfig) -> np.ndarray:
    W, H = cfg.width, cfg.height
    pad = int(W * 0.06)
    ox = int(round(pad - p.dx * W * 0.30))
    oy = int(round(pad - p.dy * H * 0.30))
    ox = max(0, min(ox, assets.backdrop.shape[1] - W))
    oy = max(0, min(oy, assets.backdrop.shape[0] - H))
    frame = assets.backdrop[oy:oy + H, ox:ox + W].copy()
    if assets.shadow_mul is not None:
        frame = (frame.astype(np.float32) * assets.shadow_mul).astype(np.uint8)

    cw, ch = assets.card_w, assets.card_h
    x0, y0 = (W - cw) // 2, (H - ch) // 2
    m = assets.card_mask
    region = frame[y0:y0 + ch, x0:x0 + cw].astype(np.float32)
    frame[y0:y0 + ch, x0:x0 + cw] = np.clip(
        region * (1.0 - m) + card.astype(np.float32) * m, 0, 255).astype(np.uint8)
    return frame


def render_frames(spec: ShotSpec, assets: PageAssets, cfg: FilmConfig) -> Iterator[np.ndarray]:
    n = spec.frames
    for i in range(n):
        t = 0.0 if n == 1 else i / (n - 1)
        if spec.camera.mode == "fill":
            yield _stack(spec, assets, t, cfg.width, cfg.height)
        else:
            yield compose_frame(render_card(spec, assets, t),
                                assets, pose(spec, t), cfg)
