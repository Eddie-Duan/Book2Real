"""SAM (ViT-B, 1.1 GB, ~2 GB VRAM) to turn a box into a real figure outline.

The deterministic cast path cuts figures with colour keys, which is exact on a
flat paper background and useless on a painted one. SAM fixes that: give it a
box (or a coarse mask) and it returns the object outline. At 8 GB it runs at
~0.7 s per page for ViT-B, and it is only used by the layer builder — the cast
chain keeps working without it.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import _fail, cuda_free_gb

MODEL = "facebook/sam-vit-base"
_cache: dict[str, object] = {}


def available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except Exception as exc:                          # noqa: BLE001
        _fail(f"sam: transformers missing ({exc.__class__.__name__})")
        return False
    return True


def _load():
    if "sam" in _cache:
        return _cache["sam"]
    import torch
    from transformers import SamModel, SamProcessor

    model = SamModel.from_pretrained(MODEL)
    proc = SamProcessor.from_pretrained(MODEL)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).eval()
    _cache["sam"] = (model, proc, device)
    return _cache["sam"]


def unload() -> None:
    _cache.clear()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def segment(rgb: np.ndarray, points: list[tuple[float, float]] | None = None,
            boxes: list[tuple[float, float, float, float]] | None = None,
            *, max_side: int = 1024) -> np.ndarray | None:
    """Union mask (bool H x W) of the objects SAM picks for the prompts.

    Points are normalised (x, y); boxes are normalised (x0, y0, x1, y1). When
    both are given SAM uses a box prompt plus a positive point per box centre,
    which is the most reliable combination on painted art.
    """
    if not available() or cuda_free_gb() < 2.5:
        return None
    import torch

    model, proc, device = _load()
    h, w = rgb.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    small = (Image.fromarray(rgb).resize((int(w * scale), int(h * scale)), Image.BICUBIC)
             if scale < 1 else Image.fromarray(rgb))
    kwargs: dict = {"return_tensors": "pt"}
    if points:
        kwargs["input_points"] = [[[float(x) * small.width, float(y) * small.height]
                                   for x, y in points]]
        kwargs["input_labels"] = [[1] * len(points)]
    if boxes:
        kwargs["input_boxes"] = [[[float(b[0]) * small.width, float(b[1]) * small.height,
                                   float(b[2]) * small.width, float(b[3]) * small.height]
                                  for b in boxes]]
    try:
        inputs = proc(images=small, **kwargs)
    except Exception as exc:                          # noqa: BLE001
        _fail(f"sam: prompt rejected ({exc.__class__.__name__})")
        return None
    inputs = {k: (v.to(device) if torch.is_tensor(v) else v) for k, v in inputs.items()}
    try:
        with torch.inference_mode():
            out = model(**inputs, multimask_output=True)
        masks = proc.post_process_masks(
            out.pred_masks.cpu(), inputs["original_sizes"].cpu(),
            inputs["reshaped_input_sizes"].cpu())[0]          # [1, P, 3, h, w]
        scores = out.iou_scores.cpu().numpy()[0]              # [P, 3]
    except Exception as exc:                          # noqa: BLE001
        _fail(f"sam: {exc.__class__.__name__}: {exc}")
        return None

    m = masks.numpy()[0] if masks.ndim == 5 else masks.numpy()   # [P, 3, h, w]
    union = np.zeros(m.shape[-2:], bool)
    pts = m.shape[0]
    if scores.ndim == 2 and scores.shape[0] != pts:
        scores = scores.reshape(pts, -1)                       # box prompts return [3, P]
    for i in range(pts):
        row = scores[i]
        best = int(np.argmax(row))
        # keep the best mask of each prompt unless the model itself is unsure;
        # the caller's background checks are what reject nonsense
        if float(row[best]) < 0.35:
            continue
        part = m[i][best] > 0
        if part.ndim != 2:                                     # defensive: pick a plane
            part = part.reshape(part.shape[-2], part.shape[-1])
        if not part.any():
            continue
        # a prompt that swallowed the whole page is not an object
        if part.mean() > 0.8:
            continue
        union |= part
    if not union.any():
        return None
    if union.shape != (h, w):
        union = np.asarray(Image.fromarray(union.astype(np.uint8) * 255, "L")
                           .resize((w, h), Image.NEAREST)) > 127
    return union
