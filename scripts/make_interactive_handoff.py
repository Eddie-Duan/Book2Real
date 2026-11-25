#!/usr/bin/env python3
"""Package the non-git interaction demo assets for a teammate.

Includes film.mp4 + timeline.json (newest published seedance_film run),
click_targets.json, the first N official query wavs, and the handoff doc.
Does NOT include .env or API keys.
"""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAV_DIR = {"cn01": "中文绘本01", "en01": "英文绘本01"}
# The exact official query used in the recording script, by number.
DEMO_QUERIES = {"cn01": ["32"], "en01": ["30"]}


def newest_film(book: str) -> tuple[Path, Path]:
    root = ROOT / "runs" / book
    cands = []
    for base in (root / "seedance_film", root / "one_take"):
        if not base.is_dir():
            continue
        for p in base.iterdir():
            if not p.is_dir():
                continue
            film = p / "film.mp4"
            tl = p / "timeline.json"
            if not film.is_file():
                film = p / "final_movie.mp4"
            if film.is_file() and tl.is_file():
                cands.append((film.stat().st_mtime, p, film, tl))
    if not cands:
        raise SystemExit(f"no published film+timeline under runs/{book}")
    cands.sort(reverse=True)
    _, d, film, tl = cands[0]
    return film, tl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--wavs", type=int, default=10)
    a = ap.parse_args()
    book = a.book
    film, tl = newest_film(book)
    pub = film.parent
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    handoff = ROOT / "docs" / "HANDOFF_INTERACTIVE_RECORDING.md"
    clicks = ROOT / "books" / book / "click_targets.json"
    wav_dir = ROOT / "纸书智伴企业预选赛绘本素材" / "绘本素材" / WAV_DIR.get(book, "")
    wavs = []
    for n in DEMO_QUERIES.get(book, []):
        p = wav_dir / f"{n}.wav"
        if p.is_file():
            wavs.append(p)
    for w in sorted(wav_dir.glob("*.wav"))[: a.wavs]:
        if w not in wavs:
            wavs.append(w)
    wavs = wavs[: max(a.wavs, len(DEMO_QUERIES.get(book, [])))]

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        rel = f"runs/{book}/seedance_film/{pub.name}"
        z.write(film, f"{rel}/film.mp4")
        z.write(tl, f"{rel}/timeline.json")
        meta = pub / "meta.json"
        if meta.is_file():
            z.write(meta, f"{rel}/meta.json")
        if clicks.is_file():
            z.write(clicks, f"books/{book}/click_targets.json")
        if handoff.is_file():
            z.write(handoff, "HANDOFF.md")
        for w in wavs:
            z.write(w, f"query_wavs/{w.name}")
    print(f"packed {out} ({out.stat().st_size/1e6:.1f} MB)")
    print(f"  film: {film}")
    print(f"  timeline: {tl}")
    print(f"  wavs: {len(wavs)} from {wav_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())