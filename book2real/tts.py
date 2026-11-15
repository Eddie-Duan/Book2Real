"""Natural Mandarin narration.

Primary: Microsoft Edge's open-source `edge-tts` client (neural Chinese voices,
network). Fallback: Windows SAPI (offline, may be unavailable on some machines).
Everything is cached by text+voice so a re-render costs no network time.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import wave
from pathlib import Path

from .util import ensure_dir, ffprobe

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "runs" / "tts"
VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "-8%"

#: Cache cap in MB, overridable with the ``BOOK2REAL_TTS_MB`` env var.
#: A value <= 0 disables eviction (the cartesian default is 200 MB).
CACHE_MB = 200.0
#: When over the cap, evict by mtime until the cache is down to this share.
EVICT_RATIO = 2.0 / 3.0
MB = 1024 * 1024


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


def cache_key(text: str, *, voice: str = VOICE, rate: str = RATE) -> str:
    """The one cache key shared by synth()/probe(): sha256(voice|rate|text)[:20]."""
    return hashlib.sha256(f"{voice}|{rate}|{(text or '').strip()}".encode("utf-8")).hexdigest()[:20]


def cached_path(text: str, *, voice: str = VOICE, rate: str = RATE,
                out_dir: str | Path | None = None) -> Path | None:
    """Return the cached audio file for this text, or None. Never synthesises."""
    text = (text or "").strip()
    if not text:
        return None
    d = Path(out_dir) if out_dir else CACHE
    key = cache_key(text, voice=voice, rate=rate)
    for suffix in (".mp3", ".wav"):
        p = d / f"{key}{suffix}"
        try:
            if p.exists() and p.stat().st_size > 500:
                return p
        except OSError:
            continue
    return None


def engine_of(path: str | Path | None) -> str:
    """Which engine produced an audio file: mp3 = edge-tts, wav = SAPI."""
    if not path:
        return "none"
    return "edge" if Path(path).suffix.lower() == ".mp3" else "sapi"


def available_engine() -> str:
    """Best engine a fresh synthesis would use, without any network probe."""
    try:
        import edge_tts  # noqa: F401
        return "edge"
    except Exception:                            # noqa: BLE001 - optional dependency
        pass
    if (ROOT / "scripts" / "tts.ps1").exists() and shutil.which("powershell"):
        return "sapi"
    return "none"


def probe(text: str, *, voice: str = VOICE, rate: str = RATE,
          out_dir: str | Path | None = None) -> dict:
    """Report what /api/tts would do for this text, synthesising nothing.

    An empty text is a legal probe (it simply reports ``chars=0`` and a miss),
    which keeps the service gate's bare ``/api/tts?probe=1`` at 200.
    """
    text = (text or "").strip()
    hit = cached_path(text, voice=voice, rate=rate, out_dir=out_dir) if text else None
    return {"engine": engine_of(hit) if hit else available_engine(),
            "cached": hit is not None, "chars": len(text),
            "key": cache_key(text, voice=voice, rate=rate)}


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def cache_stats(cache_dir: str | Path | None = None) -> dict:
    """{"files": n, "bytes": n} for the on-disk cache."""
    d = Path(cache_dir) if cache_dir else CACHE
    total = 0
    count = 0
    try:
        for p in d.iterdir():
            if p.is_file():
                try:
                    total += p.stat().st_size
                    count += 1
                except OSError:
                    pass
    except OSError:
        pass
    return {"files": count, "bytes": total}


def _cache_limit_mb(limit_mb: float | None = None) -> float:
    if limit_mb is not None:
        try:
            return float(limit_mb)
        except (TypeError, ValueError):
            return CACHE_MB
    try:
        return float(os.environ.get("BOOK2REAL_TTS_MB", ""))
    except (TypeError, ValueError):
        return CACHE_MB


def prune_cache(limit_mb: float | None = None, target_ratio: float = EVICT_RATIO,
                cache_dir: str | Path | None = None) -> dict:
    """Evict oldest cache files (mtime LRU) down to ``target_ratio`` of the cap.

    Never raises; a limit <= 0 disables eviction.
    """
    d = Path(cache_dir) if cache_dir else CACHE
    limit = _cache_limit_mb(limit_mb)
    before = cache_stats(d)
    report = {"limit_mb": limit, "before": before["bytes"],
              "after": before["bytes"], "removed": 0}
    if limit <= 0 or before["bytes"] <= limit * MB:
        return report
    try:
        files = [p for p in d.iterdir() if p.is_file()]
    except OSError:
        return report
    files.sort(key=_mtime)                       # oldest first
    target = limit * MB * float(target_ratio)
    total = before["bytes"]
    for p in files:
        if total <= target:
            break
        try:
            size = p.stat().st_size
            p.unlink()
        except OSError:
            continue
        total -= size
        report["removed"] += 1
    report["after"] = max(0, total)
    return report


def enforce_cache_limit(limit_mb: float | None = None) -> dict:
    """Best-effort wrapper around prune_cache(); callers must not fail on it."""
    try:
        return prune_cache(limit_mb)
    except Exception as exc:                     # noqa: BLE001 - cache is best effort
        return {"error": f"{type(exc).__name__}: {exc}"}


def synth(text: str, *, voice: str = VOICE, rate: str = RATE,
          out_dir: str | Path | None = None) -> tuple[Path, float]:
    """Return (audio path, duration). Cached across runs; edge first, SAPI fallback."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    out_dir = Path(out_dir) if out_dir else CACHE
    ensure_dir(out_dir)
    hit = cached_path(text, voice=voice, rate=rate, out_dir=out_dir)
    if hit is not None:
        enforce_cache_limit()
        return hit, _seconds(hit)
    key = cache_key(text, voice=voice, rate=rate)
    mp3 = out_dir / f"{key}.mp3"
    try:
        _edge(text, mp3, voice, rate)
        if mp3.exists() and mp3.stat().st_size > 500:
            enforce_cache_limit()
            return mp3, _seconds(mp3)
    except Exception:
        mp3.unlink(missing_ok=True)
    wav = out_dir / f"{key}.wav"
    if wav.exists() and wav.stat().st_size > 500:
        enforce_cache_limit()
        return wav, _seconds(wav)
    _sapi(text, wav, voice)
    enforce_cache_limit()
    return wav, _seconds(wav)

