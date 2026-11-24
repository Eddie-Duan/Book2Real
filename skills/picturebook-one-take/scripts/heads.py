# -*- coding: utf-8 -*-
"""Crop a clean character headshot from a generated Seedream plate."""
from __future__ import annotations

import cv2
from pathlib import Path

YUNET = ".tmp/models/yunet.onnx"


def head_from_plate(plate_raw: Path, plate_720: Path, out: Path) -> bool:
    if not plate_raw.exists() or not plate_720.exists():
        return False
    small = cv2.imread(str(plate_720))
    raw = cv2.imread(str(plate_raw))
    if small is None or raw is None:
        return False
    h720, w720 = small.shape[:2]
    det = cv2.FaceDetectorYN_create(str(YUNET), "", (w720, h720), 0.5, 0.3, 5000)
    _, faces = det.detect(small)
    if faces is None or len(faces) == 0:
        return False
    f = max(faces, key=lambda z: float(z[2]) * float(z[3]))
    sx, sy = raw.shape[1] / w720, raw.shape[0] / h720
    x, y, w, h = [int(v) for v in f[:4]]
    pad = int(max(w, h) * 0.55)
    x0, y0 = max(0, int((x - pad) * sx)), max(0, int((y - pad) * sy))
    x1, y1 = min(raw.shape[1], int((x + w + pad) * sx)), min(raw.shape[0], int((y + h + pad) * sy))
    crop = raw[y0:y1, x0:x1]
    if crop.size == 0:
        return False
    cv2.imwrite(str(out), crop)
    return True
