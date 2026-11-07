"""Decide how one page becomes reader screens — and what each screen shows.

The old behaviour was mechanical: long text was cut into N chunks, and every chunk
got the same page illustration, merely zoomed. Three screens showing one drawing
is not "three beats", it is one beat printed three times (p06 shipped like that).

This module is the decision an agent is expected to make, written down so it can
be reviewed and re-run:

    python -m book2real screens --book books/charlie_lola            # report plan
    python -m book2real screens --book books/charlie_lola --apply    # write overrides

For every page it answers two questions:

1. how many screens does the text need?  (capacity at a readable type size)
2. what does each screen show?           (original / crop / regenerate / carry)

Rules, in priority order:

* a chunk too short to stand alone is **carried** into the next page instead of
  becoming a screen of its own;
* a page must not produce two screens with the same full illustration;
  the second screen either gets a real crop around what its sentence names, or is
  marked `regenerate` (needs an image model) — never a silent duplicate;
* a page that fits is one screen with the untouched art.

The plan lands in `curation.json` under `pages.<id>.split_plan`, so the build stops
guessing and the decision is diffable:

    "split_plan": {
      "screens": [{"text": "...", "visual": {"type": "regenerate", "prompt": "..."}},
                  {"text": "...", "visual": {"type": "original"}}],
      "carry": "..."           # prepended to the next page
    }

`build` obeys the plan; `plan()` produces a default proposal when none exists.
"""
from __future__ import annotations

import re
from pathlib import Path

from .util import read_json

TEXT_LIMIT = 96        # characters a 46 px screen holds comfortably
MIN_SCREEN = 22        # below this a chunk does not deserve its own screen
MAX_SCREENS = 3


def _sentence_units(text: str) -> list[str]:
    return [u for u in re.split(r"(?<=[。！？”])", text) if u and u.strip()]


def _chunks(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    if len(text) <= limit * 1.06:
        return [text]
    out, buf = [], ""
    for unit in _sentence_units(text):
        if buf and len(buf) + len(unit) > limit and len(out) < MAX_SCREENS - 1:
            out.append(buf)
            buf = ""
        buf += unit
    if buf:
        out.append(buf)
    return out


def _names(text: str, characters: list[dict]) -> list[str]:
    return [c["name"] for c in characters if c.get("name") and c["name"] in text]


def plan_page(page: dict, characters: list[dict], spots: list[dict] | None = None,
              limit: int = TEXT_LIMIT, min_screen: int = MIN_SCREEN) -> dict:
    """Return the recommended screen plan for one page, with reasons."""
    text = re.sub(r"\s+", "", page.get("text", "") or "")
    pid = page.get("id", "?")
    if not text:
        return {"page": pid, "screens": [], "carry": "", "reasons": ["empty page"]}

    chunks = _chunks(text, limit)
    if len(chunks) == 1:
        return {"page": pid,
                "screens": [{"text": chunks[0], "visual": {"type": "original"}}],
                "carry": "",
                "reasons": [f"{len(text)} chars fit one screen"]}

    reasons = [f"{len(text)} chars -> {len(chunks)} chunks at {limit}/screen"]
    # 1) short tail chunks are carried into the next page
    carry = ""
    while len(chunks) > 1 and len(chunks[-1]) < min_screen:
        carry = chunks.pop() + carry
        reasons.append(f"tail chunk of {len(carry)} chars carried to the next page")

    # 2) two screens must not show the same illustration
    screens: list[dict] = []
    seen_full = False
    for i, chunk in enumerate(chunks):
        named = _names(chunk, characters)
        if not seen_full:
            screens.append({"text": chunk, "visual": {"type": "original"}})
            seen_full = True
            reasons.append(f"screen {i + 1}: original art (first screen)")
            continue
        if spots:
            # a curated hotspot for a character this chunk names gives a real detail shot
            hit = next((s for s in spots if s.get("name") and s["name"] in chunk), None)
            if hit and hit.get("box"):
                screens.append({"text": chunk,
                                "visual": {"type": "crop", "box": hit["box"],
                                           "name": hit["name"]}})
                reasons.append(f"screen {i + 1}: crop around {hit['name']}")
                continue
        screens.append({"text": chunk,
                        "visual": {"type": "regenerate", "needs": "image model",
                                   "prompt": _prompt(chunk, named)}})
        reasons.append(f"screen {i + 1}: needs a new image (no distinct view on the page)")

    return {"page": pid, "screens": screens, "carry": carry, "reasons": reasons}


def _prompt(chunk: str, names: list[str]) -> str:
    who = "、".join(dict.fromkeys(names)) or "绘本角色"
    beat = chunk.strip("“”\"'")[:40]
    return (f"flat children's picture-book illustration, black ink outline, the same "
            f"characters as the book ({who}), depicting: {beat}")


def plan_book(book_dir: str | Path) -> dict:
    book_dir = Path(book_dir).resolve()
    reader = book_dir / "reader"
    book = read_json(reader / "book.json")
    cur_path = book_dir / "curation.json"
    cur = read_json(cur_path) if cur_path.exists() else {}
    characters = (book.get("characters") or [])
    cpages = cur.get("pages") or {}
    out = {"book": book.get("book_id", book_dir.name), "pages": {}}
    for page in book.get("pages", []):
        if page.get("kind") == "cover":
            continue
        base = page["id"].split("-")[0]
        if base in out["pages"]:
            continue
        # plan from the *curated* page text (the source of truth), not from an
        # already-split screen, otherwise a split page looks like it fits
        curated_text = (cpages.get(base) or {}).get("text")
        source = dict(page)
        if curated_text:
            source["text"] = curated_text
            source["id"] = base
        spots = (cpages.get(base) or {}).get("spots") or source.get("spots") or []
        out["pages"][base] = plan_page(source, characters, spots)
    return out


def apply_plan(book_dir, plan, *, only=None) -> int:
    """Persist the plan into curation.json (an explicit plan always wins)."""
    from .screens_apply import apply_plan as _apply

    return _apply(book_dir, plan, only=only)


def report(plan: dict) -> str:
    lines = []
    for pid, p in (plan.get("pages") or {}).items():
        n = len(p["screens"])
        if n <= 1 and not p.get("carry"):
            continue
        lines.append(f"{pid}: {n} screen(s)"
                     + (f", carry {len(p['carry'])} chars" if p.get("carry") else ""))
        for i, s in enumerate(p["screens"]):
            v = s["visual"]
            kind = v["type"]
            extra = f" -> {v.get('name')}" if v.get("name") else ""
            lines.append(f"   {i + 1}. [{kind}{extra}] {s['text'][:34]}")
        for r in p.get("reasons", []):
            lines.append(f"   · {r}")
    return "\n".join(lines) or "no page needs more than one screen"
