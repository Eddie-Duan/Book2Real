"""Depth Anything V2 (small) on an 8 GB laptop GPU.

Used for one thing: a normalised depth map of a cleaned page illustration, which
the layer compositor turns into real layered parallax (background slower than
the characters). Small is 95 MB and ~0.3 s per page at 8 GB, so it is the demo
tier; Base is a drop-in upgrade on bigger cards.

Weights are fetched from the Hugging Face cache (pre-downloaded by
`python -m book2real models`) and are never required: `predict` returns None if
torch, transformers or the weights are missing.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from . import _fail, cuda_free_gb

MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
_cache: dict[str, object] = {}


def available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except Exception as exc:                          # noqa: BLE001
        _fail(f"depth: torch/transformers missing ({exc.__class__.__name__})")
        return False
    return True


def _load():
    if "pipe" in _cache:
        return _cache["pipe"]
    import torch
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    proc = AutoImageProcessor.from_pretrained(MODEL)
    model = AutoModelForDepthEstimation.from_pretrained(MODEL, dtype=dtype)
    model = model.to(device).eval()
    _cache["pipe"] = (proc, model, device, dtype)
    return _cache["pipe"]


def unload() -> None:
    _cache.clear()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def predict(rgb: np.ndarray, *, max_side: int = 768) -> np.ndarray | None:
    """Normalised depth, 0 = far, 1 = near, same H x W as `rgb`."""
    if not available():
        return None
    import torch

    proc, model, device, dtype = _load()
    h, w = rgb.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        small = Image.fromarray(rgb).resize((max(8, int(w * scale)), max(8, int(h * scale))),
                                            Image.BICUBIC)
    else:
        small = Image.fromarray(rgb)
    inputs = proc(images=small, return_tensors="pt")
    inputs = {k: (v.to(dtype=dtype, device=device) if v.is_floating_point()
                  else v.to(device)) for k, v in inputs.items()}
    with torch.inference_mode():
        out = model(**inputs).predicted_depth
    depth = out.squeeze().float().cpu().numpy()
    depth = (depth - depth.min()) / max(1e-6, float(depth.max() - depth.min()))
    if depth.shape != (h, w):
        depth = np.asarray(Image.fromarray(depth).resize((w, h), Image.BICUBIC), np.float32)
    return depth
