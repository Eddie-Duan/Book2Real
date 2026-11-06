"""Per-character narration timings, so the reader can highlight while it reads.

`edge-tts` emits `WordBoundary` events (offset + duration in 100-ns units) when
asked with `boundary="WordBoundary"`. For Chinese each event is one character or
one short word, which is exactly the granularity the ruby text already uses.

    python -m book2real narrate --book books/charlie_lola        # build timings
    python -m book2real narrate --book books/charlie_lola --full # also synth mp3

Output: `reader/narration/<page>.json`

    {"page": "p03", "audio": "narration/p03.mp3", "duration": 3.4,
     "cues": [{"t": 0.10, "d": 0.14, "text": "我"}, ...]}

The reader maps each cue onto the pinyin token with the same index; a cue whose
text covers several tokens (a word) lights them all. Timings are cached by
(text, voice, rate) so re-running costs nothing.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "vendor") not in sys.path:
    sys.path.insert(0, str(ROOT / "vendor"))

VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "-8%"
FALLBACK = "-4%"


def _key(text: str, voice: str, rate: str) -> str:
    h = hashlib.sha256(f"{voice}|{rate}|{text}".encode("utf-8")).hexdigest()[:16]
    return h


def synth_with_cues(text: str, out_mp3: Path, *, voice: str = VOICE,
                    rate: str = RATE) -> list[dict]:
    """Synthesise `text` and return [{t, d, text}] in seconds.

    The audio is written to `out_mp3`. A voice that does not support boundaries
    (or a network that drops them) simply yields an empty cue list — the reader
    then falls back to a uniform pacing, never to an error.
    """
    import edge_tts

    cues: list[dict] = []
    chunks: list[bytes] = []

    async def gen(rate_used: str):
        nonlocal cues, chunks
        cues, chunks = [], []
        comm = edge_tts.Communicate(text, voice, rate=rate_used,
                                    boundary="WordBoundary")
        try:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    cues.append({
                        "t": round(float(chunk.get("offset", 0)) / 1e7, 3),
                        "d": round(float(chunk.get("duration", 0)) / 1e7, 3),
                        "text": chunk.get("text", ""),
                    })
        finally:
            pass

    asyncio.run(gen(rate))
    if not chunks:
        raise RuntimeError("edge-tts returned no audio")
    out_mp3.write_bytes(b"".join(chunks))
    return cues


def uniform_cues(text: str, duration: float) -> list[dict]:
    """Fallback pacing when the voice gives no boundaries: spread the characters."""
    chars = [c for c in text if c.strip()]
    if not chars or duration <= 0:
        return []
    step = duration / len(chars)
    return [{"t": round(i * step, 3), "d": round(step, 3), "text": c}
            for i, c in enumerate(chars)]


def build(book_dir: str | Path, *, voice: str = VOICE, rate: str = RATE,
          synth: bool = True, on_progress=None) -> dict:
    book_dir = Path(book_dir).resolve()
    reader = book_dir / "reader"
    out_dir = reader / "narration"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "runs" / "tts"
    cache.mkdir(parents=True, exist_ok=True)
    book = json.loads((reader / "book.json").read_text(encoding="utf-8"))

    made, cached, fallback = 0, 0, 0
    for page in book.get("pages", []):
        if page.get("kind") == "cover":
            continue
        pid = page["id"]
        text = page.get("text", "").strip()
        if not text:
            continue
        target = out_dir / f"{pid}.json"
        if synth:
            mp3 = out_dir / f"{pid}.mp3"
            key = _key(text, voice, rate)
            cpath = cache / f"{key}.mp3"
            cues = []
            if cpath.exists():
                shutil.copyfile(cpath, mp3)
                cached += 1
                # cues are stored next to the cache entry
                cj = cache / f"{key}.json"
                if cj.exists():
                    cues = json.loads(cj.read_text(encoding="utf-8"))
            else:
                try:
                    cues = synth_with_cues(text, mp3, voice=voice, rate=rate)
                    shutil.copyfile(mp3, cpath)
                    (cache / f"{key}.json").write_text(
                        json.dumps(cues, ensure_ascii=False), encoding="utf-8")
                    made += 1
                except Exception as exc:               # noqa: BLE001
                    if on_progress:
                        on_progress(f"{pid}: tts failed ({exc.__class__.__name__})")
                    continue
            dur = 0.0
            if mp3.exists():
                from .util import ffprobe  # noqa: F401
            try:
                from .util import ffprobe

                dur = float(ffprobe(mp3).get("format", {}).get("duration", 0) or 0)
            except Exception:                          # noqa: BLE001
                dur = 0.0
            if not cues:
                cues = uniform_cues(text, dur)
                fallback += 1
            payload = {"page": pid, "audio": f"narration/{pid}.mp3",
                       "duration": round(dur, 2), "voice": voice,
                       "rate": rate, "cues": cues, "source": "edge-tts"}
        else:
            # timings only: existing mp3 duration if present
            mp3 = out_dir / f"{pid}.mp3"
            dur = 0.0
            if mp3.exists():
                try:
                    from .util import ffprobe

                    dur = float(ffprobe(mp3).get("format", {}).get("duration", 0) or 0)
                except Exception:                      # noqa: BLE001
                    dur = 0.0
            cues = uniform_cues(text, dur)
            payload = {"page": pid, "audio": f"narration/{pid}.mp3" if mp3.exists() else "",
                       "duration": round(dur, 2), "voice": voice, "rate": rate,
                       "cues": cues, "source": "uniform"}
            fallback += 1
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"pages": len(list(out_dir.glob("*.json"))), "synth": made,
            "cached": cached, "uniform": fallback}
