"""Build every layered 2.5D page for a book, then index them for the reader.

    python -m book2real layers --book books/charlie_lola [--no-models]

Reads the reader package (art + cast entities) and writes:

    reader/layers/index.json      {"pages": {"p03": {...}, ...}, "stats": {...}}
    reader/layers/<page>_bg.jpg   repaired background (ghost of the figures removed)
    reader/layers/<page>_fg.png   characters, soft cut
    reader/layers/<page>_depth.png  normalised depth (when the model is available)

The reader only needs index.json; the browser swaps the still page for these
layers and moves them with the mouse. No generative redraw of the page itself.
"""
from __future__ import annotations

import time
from pathlib import Path

from .util import ensure_dir, read_json, write_json
from .vision import layers as layer_mod

# a figure with less than this share of the page is not worth a layer
MIN_FIGURE = 0.012


def _figure_boxes(book: dict, entities: dict | None, curation: dict) -> dict[str, list[tuple]]:
    """Per page: normalised boxes of the figures that should move as a near layer."""
    out: dict[str, list[tuple]] = {}
    pages = {p["id"]: p for p in book.get("pages", [])}
    for ch in curation.get("characters", []):
        crop = ch.get("crop") or {}
        pid = crop.get("page")
        box = crop.get("box")
        if pid in pages and box:
            out.setdefault(pid, []).append(tuple(float(v) for v in box))
    if entities:
        for pid, page in (entities.get("pages") or {}).items():
            if pid not in pages:
                continue
            for ent in page.get("entities", []):
                box = ent.get("box")
                if not box or float(ent.get("area", 0.0)) < MIN_FIGURE:
                    continue
                if float(box[2]) > 0.92 and float(box[3]) > 0.92:
                    continue                     # the whole page: not a figure
                if pid in out and any(_overlap(box, b) > 0.7 for b in out[pid]):
                    continue
                out.setdefault(pid, []).append(tuple(float(v) for v in box))
    return out


def _overlap(a: list[float], b: tuple) -> float:
    ax, ay, aw, ah = (float(v) for v in a)
    bx, by, bw, bh = (float(v) for v in b)
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / max(1e-6, min(aw * ah, bw * bh))


def run(book_dir: str | Path, *, use_models: bool = True, limit: int = 0,
        on_progress=None) -> dict:
    t0 = time.perf_counter()
    book_dir = Path(book_dir).resolve()
    reader = book_dir / "reader"
    book = read_json(reader / "book.json")
    cur_path = book_dir / "curation.json"
    curation = read_json(cur_path) if cur_path.exists() else {}
    ent_path = reader / "cast" / "entities.json"
    entities = read_json(ent_path) if ent_path.exists() else None

    out_dir = ensure_dir(reader / "layers")
    boxes = _figure_boxes(book, entities, curation)
    # one build per *page*, not per screen: a split page keeps its full art
    pages: dict[str, str] = {}
    for p in book.get("pages", []):
        pid = p["id"]
        base = p.get("art_page") or p.get("art")
        if base:
            pages[pid] = base
    done: dict[str, dict] = {}
    used = {"sd": 0, "ring": 0, "flat": 0}
    for pid, art in pages.items():
        if limit and len(done) >= limit:
            break
        if on_progress:
            on_progress(f"layers: {pid}")
        data = layer_mod.build(reader, pid, art, boxes.get(pid, []), out_dir,
                               use_models=use_models)
        if not data.get("layers"):
            continue
        used[data.get("inpaint", "flat")] = used.get(data.get("inpaint", "flat"), 0) + 1
        done[pid] = {
            "depth": data.get("depth", ""),
            "inpaint": data.get("inpaint", "flat"),
            "outline": data.get("outline", "box"),
            "art_w": data.get("art_w", 0),
            "art_h": data.get("art_h", 0),
            "layers": data.get("layers", []),
        }
    index = {
        "version": 1,
        "book": book.get("book_id", book_dir.name),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds": round(time.perf_counter() - t0, 2),
        "models": bool(use_models),
        "stats": {"pages": len(done), "inpaint": used},
        "pages": done,
    }
    write_json(out_dir / "index.json", index)
    return index
