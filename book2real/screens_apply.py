"""Write a screen plan into curation.json (only when the agent approves it)."""
from __future__ import annotations

import json
from pathlib import Path

from .util import read_json


def apply_plan(book_dir: str | Path, plan: dict, *, only: list[str] | None = None) -> int:
    """Persist `plan` decisions as `pages.<id>.split_plan` in curation.json.

    A page that needs no split is skipped, and a page that already carries an
    explicit plan is left alone: a decision made by a person or a stronger model
    should not be overwritten by a default proposal.
    """
    book_dir = Path(book_dir).resolve()
    cur_path = book_dir / "curation.json"
    cur = read_json(cur_path) if cur_path.exists() else {"pages": {}}
    pages = cur.setdefault("pages", {})
    written = 0
    for pid, p in (plan.get("pages") or {}).items():
        if only and pid not in only:
            continue
        existing = pages.get(pid) or {}
        if existing.get("split_plan"):
            continue
        screens = p.get("screens") or []
        carry = p.get("carry") or ""
        if len(screens) <= 1 and not carry:
            continue
        pages.setdefault(pid, {})["split_plan"] = {
            "screens": screens,
            "carry": carry,
        }
        written += 1
    cur_path.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return written
