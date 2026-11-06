"""Motion renderer for 纸间生灵 pages.

Two kinds of page motion, both rigid (nothing is warped):

* `outline` — parts cut from the page, rotated about a shoulder pivot, plus the
  page plate behind them. Rendered here by `book2real.motion_video`.
* `still`   — a page that already has a baked loop (e.g. a Recraft-repaired
  frame animated by the agent's motion rules). The reader just plays it.

A page without a loop falls back to the CSS depth parallax, so this module never
has to run for the book to work.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .util import imread


def outline_loop(plate: np.ndarray, pieces: list[dict], *, seconds: float = 2.4,
                 fps: int = 20, bg: tuple[int, int, int] = (207, 63, 47),
                 amp: float = 12.0, cycles: float = 2.0) -> list[np.ndarray]:
    """Frames of a page whose cut pieces swing about their pivots.

    `pieces`: [{"image": rgba, "offset": (x, y), "pivot": (px, py), "phase": 0.0}]
    The plate is pasted first, then each piece is rotated on its own padded canvas
    and composited — rotating after paste drags corners off the frame.
    """
    h, w = plate.shape[:2]
    n = max(2, int(round(seconds * fps)))
    frames: list[np.ndarray] = []
    for i in range(n):
        t = seconds * i / (n - 1)
        canvas = Image.fromarray(plate).convert("RGBA")
        for piece in pieces:
            img = piece["image"]
            ph = float(piece.get("phase", 0.0))
            ang = amp * np.sin(2 * np.pi * cycles * (t / seconds) + ph)
            pw, phh = img.size
            diag = int((pw * pw + phh * phh) ** 0.5) + 2
            pad = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
            pad.alpha_composite(img, ((diag - pw) // 2, (diag - phh) // 2))
            px = piece["pivot"][0] * pw + (diag - pw) / 2
            py = piece["pivot"][1] * phh + (diag - phh) / 2
            rot = pad.rotate(ang, center=(px, py), resample=Image.BICUBIC)
            canvas.alpha_composite(rot, (int(piece["offset"][0] - (diag - pw) / 2),
                                         int(piece["offset"][1] - (diag - phh) / 2)))
        frames.append(np.asarray(canvas.convert("RGB")))
    return frames


def write_gif(frames: list[np.ndarray], path, *, fps: int = 20,
              scale: float = 1.0) -> str:
    imgs = [Image.fromarray(f) for f in frames]
    if scale != 1.0:
        imgs = [im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                          Image.LANCZOS) for im in imgs]
    imgs[0].save(path, save_all=True, append_images=imgs[1:],
                 duration=max(20, int(1000 / fps)), loop=0, optimize=True)
    return str(path)


def load_piece(reader_dir, rel: str, offset, pivot, phase: float = 0.0) -> dict:
    from pathlib import Path

    img = Image.open(Path(reader_dir) / rel).convert("RGBA")
    return {"image": img, "offset": offset, "pivot": pivot, "phase": phase}
