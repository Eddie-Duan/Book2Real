"""Imaging primitives on numpy + Pillow only.

Deliberately no OpenCV: the project must run on a stock Python with numpy and
Pillow, which is also what makes it installable on the target laptop offline.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

BILINEAR = Image.BILINEAR


# --------------------------------------------------------------- conversion
def to_pil(a: np.ndarray) -> Image.Image:
    if a.dtype != np.uint8:
        a = np.clip(a, 0, 255).astype(np.uint8)
    return Image.fromarray(a)


def from_pil(im: Image.Image) -> np.ndarray:
    return np.asarray(im)


def to_gray(a: np.ndarray) -> np.ndarray:
    """RGB uint8 -> float32 luminance in 0..1."""
    im = to_pil(a).convert("L")
    return np.asarray(im, dtype=np.float32) / 255.0


# --------------------------------------------------------------- geometry
def resize(a: np.ndarray, w: int, h: int, *, down: bool = True) -> np.ndarray:
    w, h = max(1, int(w)), max(1, int(h))
    im = to_pil(a)
    rs = Image.LANCZOS if down else Image.BILINEAR
    return np.asarray(im.resize((w, h), rs))


def cover(a: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = a.shape[:2]
    s = max(w / iw, h / ih)
    r = resize(a, int(round(iw * s)), int(round(ih * s)))
    y0 = max(0, (r.shape[0] - h) // 2)
    x0 = max(0, (r.shape[1] - w) // 2)
    return r[y0:y0 + h, x0:x0 + w].copy()


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return a.copy()
    return np.asarray(to_pil(a).filter(ImageFilter.GaussianBlur(radius=float(sigma))))


def blur_f(a: np.ndarray, sigma: float) -> np.ndarray:
    """Blur a float32 image by going through 8-bit (values are 0..255 scaled)."""
    u8 = np.clip(a, 0, 255).astype(np.uint8)
    return np.asarray(to_pil(u8).filter(ImageFilter.GaussianBlur(radius=float(sigma))),
                      dtype=np.float32)


def warp(a: np.ndarray, scale: float, dx: float, dy: float,
         out_w: int, out_h: int, *, pad_frac: float = 0.22) -> np.ndarray:
    """Scale about the centre and translate by (dx,dy) in output fractions.

    Source edges are replicated first, so the result never shows a black seam.
    """
    ih, iw = a.shape[:2]
    span = max(abs(dx) * out_w, abs(dy) * out_h)
    pad = int(round(max(iw, ih) * pad_frac + span)) + 4
    return warp_padded(pad_edge(a, pad), pad, scale, dx, dy, out_w, out_h)


def pad_edge(a: np.ndarray, pad: int) -> np.ndarray:
    if a.ndim == 2:
        return np.pad(a, pad, mode="edge")
    return np.pad(a, ((pad, pad), (pad, pad), (0, 0)), mode="edge")


def warp_padded(src: np.ndarray, pad: int, scale: float, dx: float, dy: float,
                out_w: int, out_h: int, *, fit: float = 1.0) -> np.ndarray:
    """Affine transform of an already edge-padded source.

    `fit` maps source pixels into output pixels (cover), `scale` is the camera
    zoom on top of that, and the source centre lands on the output centre plus
    (dx*out_w, dy*out_h).
    """
    scale = max(float(scale) * fit, 1e-3)
    ih, iw = src.shape[:2]
    cx = (iw - 2 * pad) / 2.0 + pad
    cy = (ih - 2 * pad) / 2.0 + pad
    tx = out_w / 2.0 + dx * out_w - scale * cx
    ty = out_h / 2.0 + dy * out_h - scale * cy
    inv = 1.0 / scale
    coeffs = (inv, 0.0, -tx * inv, 0.0, inv, -ty * inv)
    im = Image.fromarray(src, "L") if src.ndim == 2 else Image.fromarray(src)
    out = im.transform((out_w, out_h), Image.AFFINE, coeffs, resample=Image.BILINEAR)
    return np.asarray(out)


# --------------------------------------------------------------- threshold
def otsu(u8: np.ndarray) -> int:
    hist = np.bincount(u8.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return 128
    omega = np.cumsum(hist)
    mu = np.cumsum(hist * np.arange(256))
    mu_t = mu[-1]
    w0 = omega / total
    w1 = 1.0 - w0
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu_t * w0 - mu) ** 2 / (w0 * w1)
    between = np.nan_to_num(between)
    return int(np.argmax(between))


def morph(mask_u8: np.ndarray, size: int, op: str = "open") -> np.ndarray:
    k = int(size) | 1
    im = Image.fromarray(mask_u8.astype(np.uint8), "L")
    if op in ("open", "erode"):
        im = im.filter(ImageFilter.MinFilter(k))
    if op in ("open", "close", "dilate"):
        im = im.filter(ImageFilter.MaxFilter(k))
    return np.asarray(im)


def feather(mask_u8: np.ndarray, sigma: float) -> np.ndarray:
    a = (mask_u8 > 127).astype(np.uint8) * 255
    return np.asarray(to_pil(a).filter(ImageFilter.GaussianBlur(radius=float(sigma))),
                      dtype=np.float32) / 255.0


def components_large(mask: np.ndarray, min_area_frac: float,
                     down: int = 4, max_iter: int = 220) -> np.ndarray:
    """Keep connected components bigger than min_area_frac (coarse label propagation)."""
    m = mask.astype(bool)
    if not m.any():
        return np.zeros_like(m)
    h, w = m.shape
    sh, sw = max(8, h // down), max(8, w // down)
    small = np.asarray(to_pil((m * 255).astype(np.uint8)).resize((sw, sh), Image.BOX)) > 90

    ids = np.where(small, np.arange(small.size).reshape(small.shape) + 1, 0).astype(np.int32)
    for _ in range(max_iter):
        prev = ids
        ids = np.maximum.reduce([
            ids, np.roll(ids, 1, 0), np.roll(ids, -1, 0),
            np.roll(ids, 1, 1), np.roll(ids, -1, 1),
        ])
        if np.array_equal(ids, prev):
            break

    counts = np.bincount(ids.ravel())
    keep = counts >= max(4, int(min_area_frac * small.size))
    keep[0] = False
    small_keep = keep[ids]
    up = np.asarray(to_pil((small_keep * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)) > 100
    return np.logical_and(up, m)


# --------------------------------------------------------------- fill
def inpaint(a: np.ndarray, hole: np.ndarray, iters: int = 26) -> np.ndarray:
    """Diffusion fill for pixels under `hole` (bool HxW), solved at half scale."""
    if not hole.any():
        return a.copy()
    h, w = a.shape[:2]
    sw, sh = max(8, w // 2), max(8, h // 2)
    small = resize(a, sw, sh).astype(np.float32)
    sm = resize((hole.astype(np.uint8) * 255), sw, sh) > 100

    base = blur(small.astype(np.uint8), max(2.0, min(sw, sh) / 18.0)).astype(np.float32)
    cur = np.where(sm[..., None], base, small)

    for _ in range(iters):
        acc = (np.roll(cur, 1, 0) + np.roll(cur, -1, 0)
               + np.roll(cur, 1, 1) + np.roll(cur, -1, 1)) * 0.25
        cur = np.where(sm[..., None], acc, cur)

    filled = resize(np.clip(cur, 0, 255).astype(np.uint8), w, h)
    out = a.copy()
    out[hole] = filled[hole]
    return out


# --------------------------------------------------------------- metrics
_LAP = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], np.float32)


def laplacian_var(gray_u8: np.ndarray) -> float:
    g = gray_u8.astype(np.float32)
    p = np.pad(g, 1, mode="edge")
    lap = (_LAP[0, 1] * p[:-2, 1:-1] + _LAP[1, 0] * p[1:-1, :-2]
           + _LAP[1, 1] * p[1:-1, 1:-1] + _LAP[1, 2] * p[1:-1, 2:]
           + _LAP[2, 1] * p[2:, 1:-1])
    return float(lap.var())
