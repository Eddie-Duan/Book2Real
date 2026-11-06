"""Region segmentation that respects the ink line.

Plain colour quantisation merges the arm with the face: both are skin, and the
quantiser happily joins them across the cheek outline. Picture books give us a
better operator — every shape is delimited by a black ink line, so the ink is a
*wall* between regions:

    ink   = dark and much darker than its surroundings (the outline)
    walls = ink dilated enough to close anti-aliased gaps
    cells = connected components of NOT walls
    region = the union of cells sharing one quantised colour, adjacent through
             the same wall is never joined

That is what makes "the arm" separate from "the face" as a measurable fact
instead of a guess, and it is the operator layer the agent's decision rests on.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def ink_mask(rgb: np.ndarray, *, luma_rel: float = 0.62, absolute_max: int = 120) -> np.ndarray:
    """Black outline pixels: dark in absolute terms AND darker than the local page."""
    grey = rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    # local paper brightness (blurred, so a line does not lower its own baseline)
    blur = np.asarray(Image.fromarray(np.clip(grey, 0, 255).astype(np.uint8), "L")
                      .filter(ImageFilter.GaussianBlur(9)), np.float32)
    dark = (grey < blur * luma_rel) & (grey < absolute_max)
    return dark


def cells(rgb: np.ndarray, *, close: int = 1) -> list[np.ndarray]:
    """Connected components of the page minus the ink line."""
    walls = ink_mask(rgb)
    if close > 0:
        k = 2 * close + 1
        walls = np.asarray(Image.fromarray((walls.astype(np.uint8) * 255), "L")
                           .filter(ImageFilter.MaxFilter(k))) > 127
    free = (~walls).astype(np.uint8)
    out: list[np.ndarray] = []
    try:
        import cv2

        n, lab, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
        for i in range(1, n):
            out.append(lab == i)
    except Exception:                                  # noqa: BLE001
        out.append(free.astype(bool))
    return out


def region_of(cell_masks: list[np.ndarray], point_px: tuple[int, int]) -> int:
    x, y = point_px
    for i, m in enumerate(cell_masks):
        if 0 <= y < m.shape[0] and 0 <= x < m.shape[1] and m[y, x]:
            return i
    return -1


def merge_like(cell_masks: list[np.ndarray], rgb: np.ndarray, seed: int,
               *, tol: float = 26.0, max_cells: int = 200) -> list[int]:
    """Cells that belong to the same shape: same colour, touching the seed."""
    if seed < 0:
        return []
    q = np.asarray(Image.fromarray(rgb).convert("RGB")).astype(np.float32)
    seed_px = q[cell_masks[seed]].reshape(-1, 3)
    if seed_px.size == 0:
        return [seed]
    base = np.median(seed_px, axis=0)
    chosen = [seed]
    # grow by adjacency: a cell joins when its colour is close to the seed AND
    # it shares a border with a chosen cell
    chosen_mask = cell_masks[seed].copy()
    changed = True
    while changed and len(chosen) < max_cells:
        changed = False
        for i, m in enumerate(cell_masks):
            if i in chosen:
                continue
            px = q[m].reshape(-1, 3)
            if px.size == 0:
                continue
            if np.linalg.norm(np.median(px, axis=0) - base) > tol:
                continue
            grown = np.asarray(Image.fromarray((chosen_mask.astype(np.uint8) * 255), "L")
                               .filter(ImageFilter.MaxFilter(3))) > 127
            if (grown & m).any():
                chosen.append(i)
                chosen_mask |= m
                changed = True
    return chosen
