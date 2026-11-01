"""Natural Mandarin narration.

Primary: Microsoft Edge's open-source `edge-tts` client (neural Chinese voices,
network). Fallback: Windows SAPI (offline, may be unavailable on some machines).
Everything is cached by text+voice so a re-render costs no network time.
"""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
import wave
from pathlib import Path

from .util import ensure_dir, ffprobe

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "runs" / "tts"
VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "-8%"


def _seconds(path: Path) -> float:
    try:
        info = ffprobe(path)
        return float(info.get("format", {}).get("duration", 0) or 0)
    except Exception:
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except Exception:
            return 0.0


def _edge(text: str, out: Path, voice: str, rate: str) -> None:
    import edge_tts

    async def run():
        comm = edge_tts.Communicate(text, voice, rate=rate)
        await comm.save(str(out))

    asyncio.run(run())


def _sapi(text: str, out: Path, voice: str) -> None:
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    ps = ROOT / "scripts" / "tts.ps1"
    subprocess.run([
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(ps), "-TextFile", str(txt), "-OutFile", str(out),
        "-Voice", "Microsoft Huihui Desktop", "-Rate", "-1",
    ], check=True, capture_output=True)
    txt.unlink(missing_ok=True)


def synth(text: str, *, voice: str = VOICE, rate: str = RATE,
          out_dir: str | Path | None = None) -> tuple[Path, float]:
    """Return (audio path, duration). Cached across runs; edge first, SAPI fallback."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    key = hashlib.sha256(f"{voice}|{rate}|{text}".encode("utf-8")).hexdigest()[:20]
    out_dir = Path(out_dir) if out_dir else CACHE
    ensure_dir(out_dir)
    mp3 = out_dir / f"{key}.mp3"
    if mp3.exists() and mp3.stat().st_size > 500:
        return mp3, _seconds(mp3)
    try:
        _edge(text, mp3, voice, rate)
        if mp3.exists() and mp3.stat().st_size > 500:
            return mp3, _seconds(mp3)
    except Exception:
        mp3.unlink(missing_ok=True)
    wav = out_dir / f"{key}.wav"
    if wav.exists() and wav.stat().st_size > 500:
        return wav, _seconds(wav)
    _sapi(text, wav, voice)
    return wav, _seconds(wav)
