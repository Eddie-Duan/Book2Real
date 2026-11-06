"""Put a hotspot on the character the sentence is talking about.

Measured: 19 of 31 screens name a character in the text but have no hotspot,
because curation only marked 7 of them. PageScout's entities have boxes but no
names, so names cannot come from the detector — they come from the recitation:

* the text says 劳拉 / 查理 / 露塔 / 咪咪 / 波特,
* the page's figure boxes are known (cast entities),
* the largest box is the subject of the page, and on a page that names exactly
  one character, that is who the hotspot belongs to.

That is a rule, not a guess model: it only fires when the text names exactly one
character. Multi-character pages keep their curated spots or stay without.
"""
from __future__ import annotations

import json
from pathlib import Path

from .util import read_json


def suggest(reader: Path) -> dict:
    book = read_json(reader / "book.json")
    ent_path = reader / "cast" / "entities.json"
    entities = read_json(ent_path) if ent_path.exists() else {"pages": {}}
    cast = read_json(reader / "characters" / "characters.json").get("characters", [])
    names = [c["name"] for c in cast if c.get("name")]
    desc = {c["name"]: c.get("desc", "") for c in cast}

    added = []
    for page in book.get("pages", []):
        if page.get("spots"):
            continue
        text = page.get("text", "")
        mentioned = [n for n in names if n in text]
        if len(mentioned) != 1:
            continue
        ents = ((entities.get("pages") or {}).get(page["id"]) or {}).get("entities", [])
        if not ents:
            continue
        # the biggest figure is the subject; require it to be reasonably large
        best = max(ents, key=lambda e: e.get("area", 0.0))
        if best.get("area", 0.0) < 0.02:
            continue
        name = mentioned[0]
        added.append({
            "page": page["id"],
            "name": name,
            "box": [round(float(v), 4) for v in best["box"]],
            "desc": desc.get(name, ""),
            "source": "text+largest-figure",
        })
    return {"suggested": added, "count": len(added),
            "pages_without_spots": sum(1 for p in book.get("pages", []) if not p.get("spots"))}


def apply(reader: Path, suggestions: list[dict]) -> int:
    """Write the suggestions into book.json (spots already there are kept)."""
    path = reader / "book.json"
    book = read_json(path)
    by_page = {}
    for s in suggestions:
        by_page.setdefault(s["page"], []).append(
            {"name": s["name"], "box": s["box"], "desc": s["desc"], "cid": s["name"]})
    n = 0
    for page in book.get("pages", []):
        if page.get("spots"):
            continue
        for s in by_page.get(page["id"], []):
            page.setdefault("spots", []).append(
                {"name": s["name"], "box": s["box"], "desc": s["desc"]})
            n += 1
    path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return n
