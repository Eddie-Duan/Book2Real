"""Face detection used only as a QA signal for portrait crops.

OpenCV ships a small Haar cascade with the wheel, so this costs no download and
works on a CPU in ~10 ms. It is used to *reject* a portrait that cuts a face in
half: if two eyes are detected but their centre sits outside the crop, the
crop is widened instead of shipped.

It is not used to name characters. Painting-style faces are frequently missed by
Haar, so "no face found" never means "not a face" here.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

_cache: dict[str, object] = {}


def _detector():
    if "cascade" in _cache:
        return _cache["cascade"]
    try:
        import cv2
    except Exception:                                 # noqa: BLE001
        return None
    cls = getattr(cv2, "CascadeClassifier", None)     # OpenCV 5 split objectdetect out
    data = getattr(getattr(cv2, "data", None), "haarcascades", "")
    if cls is None or not data:
        return None
    casc = cls(data + "haarcascade_frontalface_default.xml")
    if casc.empty():
        return None
    _cache["cascade"] = casc
    return casc


def faces(rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) boxes in pixels; empty when nothing is detected."""
    casc = _detector()
    if casc is None:
        return []
    import cv2

    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = grey.shape[:2]
    scale = 640 / max(h, w)
    if scale < 1:
        grey = cv2.resize(grey, (int(w * scale), int(h * scale)))
    found = casc.detectMultiScale(grey, scaleFactor=1.12, minNeighbors=5,
                                  minSize=(28, 28))
    inv = 1.0 / min(1.0, scale)
    return [(int(x * inv), int(y * inv), int(fw * inv), int(fh * inv))
            for x, y, fw, fh in found]


def head_fully_inside(rgb: np.ndarray, box: tuple[float, float, float, float],
                      margin: float = 0.02) -> tuple[bool, dict]:
    """True when every detected face lies inside `box` (normalised) with margin."""
    dets = faces(rgb)
    if not dets:
        return True, {"faces": 0}
    h, w = rgb.shape[:2]
    x, y, bw, bh = box
    x0, y0, x1, y1 = x * w, y * h, (x + bw) * w, (y + bh) * h
    mx, my = margin * w, margin * h
    outside = 0
    for fx, fy, fw, fh in dets:
        cx, cy = fx + fw / 2, fy + fh / 2
        if not (x0 + mx <= cx <= x1 - mx and y0 + my <= cy <= y1 - my):
            outside += 1
    return outside == 0, {"faces": len(dets), "outside": outside}


def face_center(rgb: np.ndarray) -> tuple[float, float] | None:
    dets = faces(rgb)
    if not dets:
        return None
    h, w = rgb.shape[:2]
    big = max(dets, key=lambda d: d[2] * d[3])
    return ((big[0] + big[2] / 2) / w, (big[1] + big[3] / 2) / h)
