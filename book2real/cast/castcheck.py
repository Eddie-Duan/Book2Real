"""Report characters that share a portrait, and fix the curation box that caused it.

    python -m book2real castcheck --book books/charlie_lola

This is the check that would have caught 露塔 and 咪咪 showing the same face: both
portraits were cropped from different boxes on the same page, so the wall had one
girl twice. It reports the pair and the page each crop came from; it never
rewrites curation (faces are a human/creative decision), it just makes the
mistake loud instead of silent.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..util import read_json
from ..vision.portraits import duplicate_portraits


def run(book_dir: str | Path) -> dict:
    book_dir = Path(book_dir).resolve()
    reader = book_dir / "reader"
    art_path = reader / "characters" / "characters.json"
    cards = read_json(art_path).get("characters", [])
    cur_path = book_dir / "curation.json"
    curation = read_json(cur_path) if cur_path.exists() else {}
    crop_page = {c.get("id"): (c.get("crop") or {}).get("page")
                 for c in curation.get("characters", [])}
    dups = duplicate_portraits(reader, cards)
    for d in dups:
        d["page_a"] = crop_page.get(d["a"])
        d["page_b"] = crop_page.get(d["b"])
        if d["page_a"] == d["page_b"]:
            d["hint"] = "same source page: the crops are the same drawing"
    return {"book": book_dir.name, "characters": len(cards),
            "duplicates": dups, "checked": len(cards)}


if __name__ == "__main__":
    import sys

    print(json.dumps(run(sys.argv[1] if len(sys.argv) > 1 else "."), ensure_ascii=False, indent=2))
