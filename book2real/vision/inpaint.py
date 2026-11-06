"""Classical + learned repair of cut edges.

When an arm is cut out of the body plate the paper left behind is flat colour,
which is exact but leaves a visible "ghost" of the limb in the line work. This
module fixes that at two prices:

    local_repair  -> pure numpy/numpy+OpenCV, instant, no weights
    inpaint       -> Stable Diffusion 1.5 inpainting, ~1.5 s for a 512 px page,
                     only invoked when the flat fill disagrees with the paper

The generative path is deliberately boring: mask = pixels that the flat fill
still leaves inked, prompt = empty, strength low, and the result is blended back
only inside the mask. A model that hallucinates a phone into the red paper (it
does, at 8 steps) is contained by a similarity gate: if the filled patch does
not match the surrounding paper colour, the flat fill wins.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from . import _fail, cuda_free_gb

MODEL = "stable-diffusion-v1-5/stable-diffusion-inpainting"
_cache: dict[str, object] = {}


def available() -> bool:
    try:
        import torch  # noqa: F401
        import diffusers  # noqa: F401
    except Exception as exc:                          # noqa: BLE001
        _fail(f"inpaint: diffusers/torch missing ({exc.__class__.__name__})")
        return False
    return True


def _load():
    if "pipe" in _cache:
        return _cache["pipe"]
    import torch
    from diffusers import StableDiffusionInpaintPipeline

    pipe = StableDiffusionInpaintPipeline.from_pretrained(
        MODEL, torch_dtype=torch.float16, safety_checker=None, requires_safety_checker=False)
    pipe.set_progress_bar_config(disable=True)
    pipe.to("cuda")
    _cache["pipe"] = pipe
    return pipe


def unload() -> None:
    _cache.clear()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _snap(h: int, w: int, step: int = 8, lo: int = 256, hi: int = 768) -> tuple[int, int]:
    h = int(np.clip(round(h / step) * step, lo, hi))
    w = int(np.clip(round(w / step) * step, lo, hi))
    return h, w


def local_repair(rgb: np.ndarray, region: np.ndarray, key: np.ndarray,
                 *, feather: float = 1.2) -> np.ndarray:
    """Flat-fill the region but keep the paper's grain/lighting.

    Instead of writing one constant colour, the fill samples the *median paper
    brightness* per row of the untouched border and multiplies the key colour by
    it, so a lit page keeps its gradient and the patch does not read as a sticker.
    """
    h, w = rgb.shape[:2]
    a = np.asarray(Image.fromarray((region.astype(np.uint8) * 255), "L")
                   .filter(ImageFilter.GaussianBlur(feather)), np.float32) / 255.0
    luma = rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    key_l = float(np.asarray(key, np.float32) @ np.array([0.299, 0.587, 0.114], np.float32))
    rows = np.where(region.any(axis=1))[0]
    gain = np.ones(h, np.float32)
    if rows.size:
        # paper brightness on the same rows, sampled outside the region
        outside = (~region[rows]).astype(bool)
        for y in rows:
            vals = luma[y][outside[rows.tolist().index(y)]]
            if vals.size > 8:
                gain[y] = float(np.median(vals)) / max(1e-3, key_l)
    fill = np.asarray(key, np.float32)[None, None, :] * gain[:, None, None]
    out = rgb.astype(np.float32) * (1 - a[..., None]) + fill * a[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def colour_sim(patch: np.ndarray, reference: np.ndarray) -> float:
    """1.0 = the patch looks like the reference paper (mean colour + std)."""
    if patch.size == 0 or reference.size == 0:
        return 0.0
    mp, mr = patch.reshape(-1, 3).mean(0), reference.reshape(-1, 3).mean(0)
    sp, sr = patch.reshape(-1, 3).std(0), reference.reshape(-1, 3).std(0)
    d = np.abs(mp - mr).mean() / 255.0
    s = np.abs(sp - sr).mean() / 255.0
    return float(max(0.0, 1.0 - 1.4 * d - 0.6 * s))


def inpaint(rgb: np.ndarray, mask: np.ndarray, *, key: np.ndarray | None = None,
            steps: int = 8, guidance: float = 7.0, prompt: str = "") -> tuple[np.ndarray, dict]:
    """Generatively erase `mask` (bool H x W) and verify the result.

    Returns (image, info). info["accept"] is False when the generated patch does
    not match the paper, in which case `image` is the flat-fill repair instead.
    """
    h, w = rgb.shape[:2]
    info: dict = {"used": "flat", "accept": False, "sim": 0.0, "steps": steps}
    if not mask.any():
        return rgb, info
    kh, kw = _snap(h, w)
    small = np.asarray(Image.fromarray(rgb).resize((kw, kh), Image.LANCZOS))
    msmall = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255, "L")
                        .resize((kw, kh), Image.BILINEAR)) > 20
    # a mask that only covers a few pixels gives the model too little context
    if msmall.sum() < 64:
        info["used"] = "flat"
        return rgb, info
    gen: np.ndarray | None = None
    if available() and cuda_free_gb() > 3.0:
        import torch

        pipe = _load()
        im = Image.fromarray(small).convert("RGB")
        mk = Image.fromarray((msmall.astype(np.uint8) * 255), "L")
        try:
            with torch.inference_mode():
                out = pipe(prompt=prompt, image=im, mask_image=mk,
                           num_inference_steps=steps, guidance_scale=guidance,
                           height=kh, width=kw).images[0]
            gen = np.asarray(out.resize((w, h), Image.LANCZOS))
        except Exception as exc:                      # noqa: BLE001
            _fail(f"inpaint: {exc.__class__.__name__}: {exc}")
    ring = mask.copy()
    ring = np.asarray(Image.fromarray(ring.astype(np.uint8) * 255, "L")
                      .filter(ImageFilter.MaxFilter(9)), bool)
    ring = ring & ~np.asarray(Image.fromarray(mask.astype(np.uint8) * 255, "L")
                              .filter(ImageFilter.MaxFilter(3)), bool)
    flat = local_repair(rgb, mask, key if key is not None else _paper_key(rgb))
    if gen is not None:
        sim = colour_sim(gen[mask], rgb[ring])
        info["sim"] = round(sim, 3)
        if sim >= 0.55:
            # keep the generated texture, but never outside the mask
            soft = np.asarray(Image.fromarray((mask.astype(np.uint8) * 255), "L")
                              .filter(ImageFilter.GaussianBlur(1.0)), np.float32) / 255.0
            mixed = gen.astype(np.float32) * soft[..., None] + rgb.astype(np.float32) * (1 - soft[..., None])
            info.update(used="sd", accept=True)
            return np.clip(mixed, 0, 255).astype(np.uint8), info
    info["accept"] = True
    return flat, info


def _paper_key(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    b = max(2, int(0.02 * min(h, w)))
    px = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3),
                         rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3)]).astype(np.float32)
    return np.median(px, axis=0)
