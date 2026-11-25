# -*- coding: utf-8 -*-
"""Cover intro: 2s clean title card + spoken book title, prepended to a film.

Generic for any book: title/language come from books/<id>/book.json; voice from
MINIMAX_VOICE_ID / MINIMAX_VOICE_ID_EN. This is post-production (Q43 caveat),
intended for the preliminary round / presentation layer.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import tempfile
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from common import ROOT, load_env

FONT = "C:/Windows/Fonts/msyhbd.ttc"


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("cmd failed: " + " ".join(cmd) + "\n" + p.stderr[-1200:])
    return p.stdout


def _probe(path: Path) -> dict:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json",
                "-show_streams", "-show_format", str(path)])
    return json.loads(out)


def _duration(path: Path) -> float:
    return float((_probe(path).get("format") or {}).get("duration") or 0)


def tts_title(text: str, language: str, out: Path, voice: str | None = None) -> float:
    load_env()
    key = os.environ.get("MINIMAX_API_KEY", "")
    if not key:
        raise RuntimeError("MINIMAX_API_KEY missing; cannot read the title")
    gid = os.environ.get("MINIMAX_GROUP_ID", "")
    if not voice:
        voice = (os.environ.get("MINIMAX_VOICE_ID_EN") if language == "en"
                 else os.environ.get("MINIMAX_VOICE_ID")) or "Chinese (Mandarin)_Warm_Girl"
    body = {
        "model": os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-turbo"),
        "text": text,
        "voice_setting": {
            "voice_id": voice,
            "speed": float(os.environ.get("MINIMAX_SPEED", "0.95")),
            "vol": float(os.environ.get("MINIMAX_VOL", "1.0")),
            "pitch": int(float(os.environ.get("MINIMAX_PITCH", "0"))),
            "emotion": os.environ.get("MINIMAX_EMOTION", "calm"),
        },
        "language_boost": os.environ.get("MINIMAX_LANGUAGE_BOOST", "auto"),
        "stream": False,
        "audio_setting": {"sample_rate": 24000, "format": "wav", "channel": 1},
    }
    params = {"GroupId": gid} if gid else {}
    r = requests.post(os.environ.get("MINIMAX_HOST", "https://api.minimaxi.com").rstrip("/")
                      + "/v1/t2a_v2", params=params,
                      headers={"Authorization": "Bearer " + key,
                               "Content-Type": "application/json"}, json=body, timeout=120)
    obj = r.json()
    base = obj.get("base_resp") or {}
    if r.status_code != 200 or base.get("status_code") not in (None, 0):
        raise RuntimeError("TTS failed: " + r.text[:500])
    raw = bytes.fromhex((obj.get("data") or {}).get("audio") or "")
    if not raw:
        raise RuntimeError("TTS returned no audio")
    out.write_bytes(raw)
    return _duration(out)


def cover_png(title: str, subtitle: str, out: Path, size=(1280, 720)) -> None:
    w, h = size
    im = Image.new("RGB", (w, h), "#f6ecd9")
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=(int(255 - 25 * t), int(250 - 39 * t), int(240 - 56 * t)))
    d.ellipse([90, 30, 270, 210], fill=(255, 250, 240))
    d.ellipse([970, 480, 1230, 740], fill=(243, 230, 210))
    size_px = 76 if len(title) <= 12 else 58
    tf = ImageFont.truetype(FONT, size_px)
    sf = ImageFont.truetype(FONT, 26)
    bbox = d.textbbox((0, 0), title, font=tf)
    d.text(((w - (bbox[2] - bbox[0])) / 2, 290), title, font=tf, fill=(91, 70, 54))
    sb = d.textbbox((0, 0), subtitle, font=sf)
    d.text(((w - (sb[2] - sb[0])) / 2, 410), subtitle, font=sf, fill=(140, 112, 82))
    im.save(out)


def add_cover_intro(book_dir: Path, movie: Path, out: Path, seconds: float = 2.0,
                    voice: str | None = None, subtitle: str | None = None) -> dict:
    """Freeze the film's first frame for N seconds, speak the book title, then play it."""
    book = json.loads((book_dir / "book.json").read_text(encoding="utf-8-sig"))
    title = str(book.get("title") or book_dir.name)
    lang = str(book.get("language") or "zh")
    subtitle = subtitle or ("Paper Book Companion · Picture Book Film" if lang == "en"
                            else "纸书智伴 · 绘本影片")
    work = Path(tempfile.mkdtemp(prefix=f"cover_{book_dir.name}_"))
    wav = work / "title.wav"
    tts_sec = tts_title(title, lang, wav, voice)
    cover_sec = max(seconds, min(3.0, tts_sec + 0.12))
    # cover = the film's own first frame (frozen), not a generated title card
    png = work / "first_frame.png"
    _run(["ffmpeg", "-v", "error", "-y", "-ss", "0", "-i", str(movie),
          "-frames:v", "1", str(png)])
    clip = work / "cover.mp4"
    _run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "24", "-t", str(cover_sec),
          "-i", str(png), "-i", str(wav),
          "-filter_complex", f"[1:a]volume=3.8,apad=pad_dur={cover_sec},"
                             f"atrim=0:{cover_sec},asetpts=N/SR/TB[a]",
          "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
          "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-ar", "48000", "-ac", "2",
          "-b:a", "192k", "-shortest", str(clip)])
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-i", str(movie),
          "-filter_complex", "[0:v]fps=24,format=yuv420p[v0];[1:v]fps=24,format=yuv420p[v1];"
                             "[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]",
          "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
          "-r", "24", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
          "-movflags", "+faststart", str(out)])
    return {"title": title, "language": lang, "voice": voice or "",
            "cover_seconds": round(cover_sec, 3), "tts_seconds": round(tts_sec, 3),
            "out": str(out)}


def shift_timeline(timeline: dict, cover_seconds: float) -> dict:
    tl = copy.deepcopy(timeline)
    old = [s for s in tl.get("segments") or [] if not (s.get("kind") == "cover" and float(s.get("t1", 0)) < 0.01)]
    for s in old:
        s["t0"] = round(float(s["t0"]) + cover_seconds, 3)
        s["t1"] = round(float(s["t1"]) + cover_seconds, 3)
    w = int(tl.get("width") or 1280); h = int(tl.get("height") or 720)
    cover = {"kind": "cover", "id": "p01", "source_page": 1, "segment_id": "cover",
             "t0": 0.0, "t1": round(cover_seconds, 3), "art_rect": [0, 0, w, h],
             "source_w": 1, "source_h": 1, "mapping": "identity",
             "text": tl.get("title") or "", "scene": "封面", "spots": [], "ocr_lines": []}
    tl["segments"] = [cover] + old
    tl["duration"] = round(float(tl.get("duration") or old[-1]["t1"]) + cover_seconds, 3)
    return tl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--movie", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeline", default=None)
    ap.add_argument("--out-timeline", default=None)
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--voice", default=None)
    a = ap.parse_args()
    meta = add_cover_intro(ROOT / a.book, Path(a.movie), Path(a.out), a.seconds, a.voice)
    if a.timeline:
        tl = json.loads(Path(a.timeline).read_text(encoding="utf-8"))
        tl2 = shift_timeline(tl, meta["cover_seconds"])
        out_tl = Path(a.out_timeline or (Path(a.timeline).parent / "timeline_cover.json"))
        out_tl.write_text(json.dumps(tl2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        meta["timeline"] = str(out_tl)
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())