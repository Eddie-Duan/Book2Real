"""SAM prompts that do not swallow the page.

One box prompt for a crowd (p05: three children) returns the union of three
objects; then the "stay inside your prompt" gate sees most of the mask outside
the union and abandons SAM entirely. The fix is to ask for one object at a time:
one box per figure, best mask taken *per prompt*, and every prompt's mask kept
only if it stays inside its own box.
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




def _post(proc, out, inputs) -> np.ndarray:
    """post_process_masks -> numpy [num_prompts, 3, h, w] on every transformers version."""
    raw = proc.post_process_masks(out.pred_masks.cpu(), inputs["original_sizes"].cpu(),
                                  inputs["reshaped_input_sizes"].cpu())
    if isinstance(raw, (list, tuple)):
        raw = raw[0]
    arr = raw.numpy() if hasattr(raw, "numpy") else np.asarray(raw)
    if arr.ndim == 5:
        arr = arr[0]
    return arr

def segment_each(rgb: np.ndarray, boxes: list[tuple[float, float, float, float]],
                 *, max_side: int = 1024, keep_share: float = 0.18) -> list[np.ndarray]:
    """One mask per box (normalised x0, y0, x1, y1). Empty list when unusable.

    Each mask is kept only if at least `keep_share` of it lies inside its own
    box — that is what rejects a mask that latched onto the sofa or wallpaper.

    Boxes are submitted one at a time: transformers 5 batches multi-box prompts
    into a shape its own processor rejects ("Sizes of tensors must match"), and
    one call per figure is also what makes the per-box sanity check meaningful.
    """
    if not available() or cuda_free_gb() < 2.5 or not boxes:
        return []
    out: list[np.ndarray] = []
    for box in boxes:
        part = _one_box(rgb, box, max_side=max_side, keep_share=keep_share)
        if part is not None:
            out.append(part)
    return out


def _one_box(rgb: np.ndarray, box: tuple[float, float, float, float], *,
             max_side: int, keep_share: float) -> np.ndarray | None:
    import torch

    model, proc, device = _load()
    h, w = rgb.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    small = (Image.fromarray(rgb).resize((int(w * scale), int(h * scale)), Image.BICUBIC)
             if scale < 1 else Image.fromarray(rgb))
    sw, sh = small.size
    x0, y0, x1, y1 = box
    inputs = proc(images=small,
                  input_boxes=[[[float(x0) * sw, float(y0) * sh,
                                 float(x1) * sw, float(y1) * sh]]],
                  return_tensors="pt")
    inputs = {k: (v.to(device) if torch.is_tensor(v) else v) for k, v in inputs.items()}
    try:
        with torch.inference_mode():
            res = model(**inputs, multimask_output=True)
        masks = _post(proc, res, inputs)
        scores = np.asarray(res.iou_scores.cpu().numpy()).reshape(masks.shape[0], -1)
    except Exception as exc:                          # noqa: BLE001
        _fail(f"sam: {exc.__class__.__name__}: {exc}")
        return None
    best = int(np.argmax(scores[0]))
    if float(scores[0][best]) < 0.35:
        return None
    part = masks[0][best] > 0
    if part.ndim != 2:
        part = part.reshape(part.shape[-2], part.shape[-1])
    if part.shape != (h, w):
        part = np.asarray(Image.fromarray((part * 255).astype(np.uint8), "L")
                          .resize((w, h), Image.NEAREST)) > 127
    if not part.any() or part.mean() > 0.8:
        return None
    ref = np.zeros((h, w), bool)
    ref[int(max(0, (y0 - 0.03) * h)):int(min(h, (y1 + 0.03) * h)),
        int(max(0, (x0 - 0.03) * w)):int(min(w, (x1 + 0.03) * w))] = True
    inside = float(part[ref].sum()) / max(1.0, float(part.sum()))
    if inside < keep_share:
        return None
    return part


def segment(rgb: np.ndarray, points: list[tuple[float, float]] | None = None,
            boxes: list[tuple[float, float, float, float]] | None = None,
            *, max_side: int = 1024) -> np.ndarray | None:
    """Union mask for point prompts, or the union of per-box masks."""
    if boxes:
        parts = segment_each(rgb, boxes, max_side=max_side)
        if not parts:
            return None
        out = np.zeros(parts[0].shape, bool)
        for p in parts:
            out |= p
        return out
    if not points:
        return None
    if not available() or cuda_free_gb() < 2.5:
        return None
    import torch

    model, proc, device = _load()
    h, w = rgb.shape[:2]
    small = Image.fromarray(rgb)
    try:
        inputs = proc(images=small, input_points=[[[float(x) * w, float(y) * h]
                                                   for x, y in points]],
                      input_labels=[[1] * len(points)], return_tensors="pt")
        inputs = {k: (v.to(device) if torch.is_tensor(v) else v) for k, v in inputs.items()}
        with torch.inference_mode():
            out = model(**inputs, multimask_output=True)
        masks = _post(proc, out, inputs)
        scores = out.iou_scores.cpu().numpy()
    except Exception as exc:                          # noqa: BLE001
        _fail(f"sam: {exc.__class__.__name__}: {exc}")
        return None
    m = masks
    if scores.ndim == 3:
        scores = scores[0]
    best = int(np.argmax(scores if scores.ndim == 1 else scores[0]))
    part = m[0][best] > 0
    return part if part.any() else None
