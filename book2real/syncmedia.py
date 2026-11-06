"""Keep the reader's generated assets in sync with a book rebuild.

`book2real layers` rewrites `reader/layers/index.json` from scratch, which drops
the `motion_gif` links the agent tools added. This module re-attaches them by
scanning `reader/motion_gif/<page>*.gif`, so a rebuild never silently loses the
animation a page already has.

    python -m book2real sync --book books/charlie_lola
"""
from __future__ import annotations

from pathlib import Path

from .util import read_json, write_json

# preferred order when several loops exist for one page
PREFER = ("left_wave", "wave", "breathe")


def sync(book_dir: str | Path) -> dict:
    book_dir = Path(book_dir).resolve()
    reader = book_dir / "reader"
    gif_dir = reader / "motion_gif"
    idx_path = reader / "layers" / "index.json"
    if not idx_path.exists():
        return {"error": "no layers/index.json"}
    index = read_json(idx_path)
    pages = index.get("pages", {})
    attached, missing = {}, []
    if gif_dir.exists():
        gifs = sorted(gif_dir.glob("*.gif"))
        for pid in pages:
            mine = [g for g in gifs if g.stem == pid or g.stem.startswith(pid + "_")]
            if not mine:
                continue
            # a real limb loop beats a page-level breathe loop
            def rank(g: Path) -> int:
                for i, tag in enumerate(PREFER):
                    if tag in g.stem:
                        return i
                return len(PREFER)
            best = sorted(mine, key=rank)[0]
            pages[pid]["motion_gif"] = f"motion_gif/{best.name}"
            attached[pid] = best.name
    for pid, rec in pages.items():
        want = rec.get("motion_gif")
        if want and not (reader / want).exists():
            rec["motion_gif"] = ""
            missing.append(pid)
    write_json(idx_path, index)
    return {"attached": attached, "dropped": missing, "pages": len(pages)}
