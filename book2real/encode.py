"""Shot encoding: frames -> h264 clip, plus decoding helpers for QC."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterator

import numpy as np

from .util import ensure_dir, ffmpeg_path


def encode_frames(frames: Iterator[np.ndarray], path: str | Path, fps: int,
                  size: tuple[int, int], *, crf: int = 18, preset: str = "veryfast",
                  log_path: str | Path | None = None) -> Path:
    """Pipe RGB frames into libx264. Returns the clip path."""
    path = Path(path)
    ensure_dir(path.parent)
    w, h = size
    cmd = [
        ffmpeg_path(), "-hide_banner", "-nostdin", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
        "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(path),
    ]
    logf = open(log_path, "wb") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=logf)
    try:
        for f in frames:
            if f.shape[0] != h or f.shape[1] != w:
                from . import imgops as I
                f = I.resize(f, w, h)
            proc.stdin.write(np.ascontiguousarray(f, dtype=np.uint8).tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    rc = proc.wait()
    if logf is not subprocess.DEVNULL:
        logf.close()
    if rc != 0:
        raise RuntimeError(f"encode failed for {path} (exit {rc})")
    return path


def grab_frames(video: str | Path, size: tuple[int, int], *,
                gray: bool = True, max_frames: int = 400) -> list[np.ndarray]:
    """Decode a clip at a small size for QC (bounded frame count)."""
    w, h = size
    pix = "gray" if gray else "rgb24"
    cmd = [
        ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(video),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=disable",
        "-pix_fmt", pix, "-f", "rawvideo", "pipe:1",
    ]
    out = subprocess.run(cmd, capture_output=True)
    if out.returncode != 0:
        raise RuntimeError(f"decode failed: {video}")
    n_ch = 1 if gray else 3
    fb = w * h * n_ch
    buf = out.stdout
    n = min(max_frames, len(buf) // fb)
    if n == 0:
        return []
    arr = np.frombuffer(buf[: n * fb], np.uint8).reshape(n, h, w, n_ch)
    return [arr[i, :, :, 0] if gray else arr[i] for i in range(n)]
