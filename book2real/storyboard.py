"""Storyboard: book.json -> shot list. This is where the "director" lives."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import Camera, ShotSpec, TextCue
from .util import clamp

MOVES = ["dolly_in", "pan_lr", "kenburns", "dolly_out", "pan_rl", "tilt_up", "drift"]


def load_book(book_dir: str | Path) -> dict:
    book_dir = Path(book_dir)
    meta_path = book_dir / "book.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"missing {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.setdefault("book_id", book_dir.name)
    meta.setdefault("title", meta["book_id"])
    meta.setdefault("fps", 24)
    pages = meta.get("pages") or []
    if not pages:
        raise ValueError("book.json has no pages")
    return meta


def plan(book_dir: str | Path, *, fps: int | None = None,
         b_track: bool = True, hero_per: float = 0.75) -> tuple[dict, list[ShotSpec]]:
    """Turn pages into shots.

    Policy: pages flagged `hero` (or the most salient ones) get the B track,
    everything else gets the A track. Keeping B for a minority of shots is
    what holds the 15-minute budget together.
    """
    book_dir = Path(book_dir)
    meta = load_book(book_dir)
    fps = int(fps or meta.get("fps", 24))
    shots: list[ShotSpec] = []

    pages = meta["pages"]
    for i, pg in enumerate(pages, start=1):
        page_no = int(pg.get("page", i))
        text = (pg.get("text") or "").strip()
        dur = float(pg.get("duration") or clamp(3.2 + 0.20 * len(text), 3.6, 6.2))
        dur = round(dur * 2) / 2

        hero = bool(pg.get("hero", False)) or float(pg.get("salience", 0)) >= hero_per
        want_track = pg.get("track")
        if want_track:
            track = want_track
        elif hero and b_track:
            track = "procedural"
        else:
            track = "parallax"

        move = pg.get("move") or MOVES[(page_no - 1) % len(MOVES)]
        amount = float(pg.get("amount", 0.10 if hero else 0.075))
        mode = pg.get("mode", "card")
        parallax = float(pg.get("parallax", 0.55 if hero else 0.45))

        spec = ShotSpec(
            shot_id=f"p{page_no:02d}",
            page=page_no,
            duration=dur,
            fps=fps,
            track=track,
            camera=Camera(move=move, amount=amount, mode=mode, parallax=parallax),
            motion=dict(pg.get("motion") or {}),
            text_cues=[TextCue(start=0.20, end=max(0.6, dur - 0.25), text=text).__dict__] if text else [],
            budget_class="hero" if hero else "standard",
            seed=int(pg.get("seed", 1000 + page_no)),
            prompt=pg.get("prompt", ""),
            page_image=str(book_dir / "pages" / pg["file"]),
        )
        shots.append(spec)

    coverage = len({s.page for s in shots})
    meta["_coverage"] = coverage / max(1, len(pages))
    return meta, shots


def timeline(shots: list[ShotSpec], clips: dict, *, title_seconds: float = 0.0) -> list[dict]:
    """Global chapter table for the player: start/end/page/text/shape."""
    out, t = [], float(title_seconds)
    for s in shots:
        c = clips.get(s.shot_id)
        dur = c.duration if c else s.duration
        out.append({
            "shot_id": s.shot_id, "page": s.page,
            "start": round(t, 3), "end": round(t + dur, 3),
            "text": (s.text_cues[0]["text"] if s.text_cues else ""),
            "track": c.backend.get("id") if c else s.track,
            "status": c.status if c else "missing",
        })
        t += dur
    return out
