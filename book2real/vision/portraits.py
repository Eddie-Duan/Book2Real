"""Critic extension: portrait QA for the character wall.

Two failure modes the user actually hit:

* two different characters showing the same face (咪咪's crop was taken from the
  page where 露塔 is drawn, so the wall had 露塔 twice);
* a portrait that is mostly background/petri dish instead of a head.

Both are checked with colour statistics, not a model: a face-to-face comparison
within one book is cheap and catches the duplicate without knowing who anyone is.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

DUP_MAX = 0.86          # histogram intersection above this = same picture


def _hist(rgb: np.ndarray, bins: int = 8) -> np.ndarray:
    h, _ = np.histogramdd(rgb.reshape(-1, 3).astype(float), bins=(bins, bins, bins),
                          range=((0, 256),) * 3)
    total = h.sum()
    return (h / total if total else h).reshape(-1)


def similarity(a: np.ndarray, b: np.ndarray, bins: int = 8) -> float:
    return float(np.minimum(_hist(a, bins), _hist(b, bins)).sum())


def duplicate_portraits(reader_dir: Path, cards: list[dict]) -> list[dict]:
    """Pairs of characters whose portraits are (nearly) the same picture."""
    loaded: list[tuple[str, np.ndarray]] = []
    for card in cards:
        rel = card.get("portrait") or ""
        path = Path(reader_dir) / rel if rel else None
        if path and path.exists():
            loaded.append((card["id"], np.asarray(Image.open(path).convert("RGB"), np.uint8)))
    out: list[dict] = []
    for i, (aid, a) in enumerate(loaded):
        for bid, b in loaded[i + 1:]:
            sim = similarity(a, b)
            if sim >= DUP_MAX:
                out.append({"a": aid, "b": bid, "similarity": round(sim, 3)})
    return out


def face_share(rgb: np.ndarray) -> float:
    """Share of the portrait inside the central region (a head is centred)."""
    h, w = rgb.shape[:2]
    inner = rgb[int(0.12 * h):int(0.88 * h), int(0.12 * w):int(0.88 * w)]
    if inner.size == 0:
        return 0.0
    return float(inner.reshape(-1, 3).std(axis=0).mean())
