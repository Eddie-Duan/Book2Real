# -*- coding: utf-8 -*-
"""Lossless concat (stream copy only). No re-encode, no creative editing."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from common import ffprobe, jread, jwrite


def concat(chunks: list[Path], out: str | Path) -> Path:
    out = Path(out)
    lst = out / "concat.txt"
    lines = []
    for p in chunks:
        ap = p.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{ap}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dest = out / "final_movie.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
           "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(dest)]
    subprocess.run(cmd, check=True)
    return dest


def assemble(out: str | Path) -> dict:
    out = Path(out)
    script = jread(out / "shot_script.json")
    chunks = [out / "chunks" / f"{c['id']}.mp4" for c in script["chunks"]]
    missing = [str(p) for p in chunks if not p.exists()]
    if missing:
        raise FileNotFoundError(f"missing chunks: {missing}")
    dest = concat(chunks, out)
    info = ffprobe(dest)
    v = [s for s in info["streams"] if s.get("codec_type") == "video"][0]
    a = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    dur = float(info["format"]["duration"])
    meta = {"file": str(dest), "duration": round(dur, 3), "width": v["width"],
            "height": v["height"], "fps": v["r_frame_rate"], "audio": bool(a),
            "chunks": [{"id": c["id"], "duration": c["duration"],
                        "file": str(chunks[i].name)} for i, c in enumerate(script["chunks"])],
            "concat": "ffmpeg -f concat -c copy (stream copy, no re-encode)"}
    jwrite(out / "final_movie.json", meta)
    return meta
