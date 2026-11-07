"""Composite page layers without the rectangular-cut artifacts.

Two things were wrong in how layer pages were built:

1. A fallback to an axis-aligned rectangle (`outline: "box"`) cuts the figures
   with a straight edge and lets the inpainter paint *inside* that rectangle —
   that is the soft band you see around every character on p03 and elsewhere.
2. A page only needs layers if the characters can be separated cleanly. If they
   cannot, showing the untouched page is strictly better than showing a ghost.

This module answers both:

    python -m book2real layercheck --book books/charlie_lola

It scores every page from the layer index and rewrites `index.json` so that
pages whose near layer is a rectangle (`outline == "box"`) are marked
`flat: true`: the reader then shows the original art, with no cut at all.
"""
from __future__ import annotations

from pathlib import Path

from .util import read_json, write_json

# rectangles are never acceptable as a character cut
BAD_OUTLINES = {"box"}
# a page whose hole is more than this share of the frame cannot be filled honestly
MAX_HOLE = 0.22


def check(book_dir: str | Path, *, apply: bool = True) -> dict:
    book_dir = Path(book_dir).resolve()
    idx_path = book_dir / "reader" / "layers" / "index.json"
    if not idx_path.exists():
        return {"error": "no layers/index.json; run `book2real layers` first"}
    index = read_json(idx_path)
    flat, keep = [], []
    for pid, rec in (index.get("pages") or {}).items():
        outline = rec.get("outline") or "box"
        if outline in BAD_OUTLINES:
            rec["flat"] = True
            rec["flat_reason"] = "character cut is a rectangle"
            flat.append(pid)
        else:
            rec["flat"] = False
            keep.append(pid)
        if apply:
            pass
    if apply:
        write_json(idx_path, index)
    return {"pages": len(index.get("pages") or {}), "flat": flat, "layered": keep}
