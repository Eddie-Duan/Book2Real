"""Small shared helpers: hashing, atomic IO, timers, easing, ffmpeg wrapper."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------- hashing
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            blk = f.read(chunk)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_json(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


# ---------------------------------------------------------------- io
def ensure_dir(p: str | Path) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(p: str | Path) -> Any:
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(p: str | Path, obj: Any) -> Path:
    p = Path(p)
    ensure_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return p


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def imread(path: str | Path) -> "Any":
    """Read an image as an RGB uint8 array (Pillow; unicode-safe on Windows)."""
    from PIL import Image
    import numpy as np

    with Image.open(str(path)) as im:
        return np.asarray(im.convert("RGB"))


def imwrite(path: str | Path, img) -> None:
    """Write an RGB uint8 array as an image (Pillow; unicode-safe on Windows)."""
    from PIL import Image

    p = Path(path)
    ensure_dir(p.parent)
    Image.fromarray(img).save(str(p), quality=88)


def human(sec: float) -> str:
    sec = float(sec)
    if sec < 60:
        return f"{sec:.1f}s"
    m, s = divmod(int(round(sec)), 60)
    return f"{m}m{s:02d}s"


class EventLog:
    """Append-only jsonl event stream — the audit trail of a run."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        ensure_dir(self.path.parent)

    def emit(self, kind: str, **fields: Any) -> None:
        rec = {"t": round(time.time(), 3), "at": now_iso(), "kind": kind, **fields}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


@contextmanager
def timer():
    box = {"wall_s": 0.0}
    t0 = time.perf_counter()
    try:
        yield box
    finally:
        box["wall_s"] = round(time.perf_counter() - t0, 3)


# ---------------------------------------------------------------- math
def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def smoothstep(t: float) -> float:
    t = clamp(t)
    return t * t * (3.0 - 2.0 * t)


def ease(t: float, kind: str = "in_out") -> float:
    t = clamp(t)
    if kind == "linear":
        return t
    if kind == "in":
        return t * t
    if kind == "out":
        return 1.0 - (1.0 - t) ** 2
    return smoothstep(t)


# ---------------------------------------------------------------- ffmpeg
def ffmpeg_path() -> str:
    p = os.environ.get("BOOK2REAL_FFMPEG") or shutil.which("ffmpeg")
    if not p:
        raise RuntimeError(
            "ffmpeg not found. Install it (`winget install Gyan.FFmpeg`) "
            "or set the BOOK2REAL_FFMPEG environment variable."
        )
    return p


def run_ffmpeg(args: Iterable[str], *, log_path: Path | None = None) -> None:
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-14:])
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}):\n{tail}")
    if log_path:
        Path(log_path).write_text(proc.stderr or "", encoding="utf-8")


def ffprobe(path: str | Path) -> dict:
    exe = Path(ffmpeg_path()).with_name("ffprobe.exe")
    if not exe.exists():
        w = shutil.which("ffprobe")
        if not w:
            return {}
        exe = Path(w)
    out = subprocess.run(
        [str(exe), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return json.loads(out.stdout or "{}")
    except Exception:
        return {}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
