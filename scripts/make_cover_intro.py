#!/usr/bin/env python3
"""Add a 2s clean cover + spoken title to a finished film (post-production).

This is an EDIT, intended for the preliminary round only. The original film is
left untouched; output is a new file.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("cmd failed: " + " ".join(cmd) + "\n" + p.stderr[-1200:])
    return p.stdout


def probe_dur(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def tts(text: str, voice: str, out: Path) -> None:
    host = os.environ.get("MINIMAX_HOST", "https://api.minimaxi.com")
    gid = os.environ.get("MINIMAX_GROUP_ID", "")
    key = os.environ.get("MINIMAX_API_KEY", "")
    if not key:
        raise SystemExit("MINIMAX_API_KEY missing")
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
    r = requests.post(host.rstrip("/") + "/v1/t2a_v2",
                      params=params, headers={"Authorization": "Bearer " + key,
                      "Content-Type": "application/json"}, json=body, timeout=120)
    obj = r.json()
    base = obj.get("base_resp") or {}
    if r.status_code != 200 or base.get("status_code") not in (None, 0):
        raise SystemExit("TTS failed: " + r.text[:500])
    raw = bytes.fromhex((obj.get("data") or {}).get("audio") or "")
    if not raw:
        raise SystemExit("TTS returned no audio")
    out.write_bytes(raw)


def cover_png(title: str, subtitle: str, out: Path, size=(1280, 720)) -> None:
    w, h = size
    im = Image.new("RGB", (w, h), "#f6ecd9")
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(255 * (1 - t) + 230 * t)
        g = int(250 * (1 - t) + 211 * t)
        b = int(240 * (1 - t) + 184 * t)
        d.line([(0, y), (w, y)], fill=(r, g, b))
    # soft decorative circles
    for cx, cy, rad, col in [(180, 120, 90, (255, 250, 240)), (1100, 610, 130, (243, 230, 210))]:
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col)
    font_path = "C:/Windows/Fonts/msyhbd.ttc"
    tf = ImageFont.truetype(font_path, 76 if len(title) <= 12 else 60)
    sf = ImageFont.truetype(font_path, 26)
    bbox = d.textbbox((0, 0), title, font=tf)
    tw = bbox[2] - bbox[0]
    d.text(((w - tw) / 2, 290), title, font=tf, fill=(91, 70, 54))
    sb = d.textbbox((0, 0), subtitle, font=sf)
    sw = sb[2] - sb[0]
    d.text(((w - sw) / 2, 410), subtitle, font=sf, fill=(140, 112, 82))
    im.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--movie", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--subtitle", default="纸书智伴 · 绘本影片")
    ap.add_argument("--voice", default="")
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    movie = Path(a.movie)
    out = Path(a.out)
    work = Path(tempfile.mkdtemp(prefix=f"cover_{a.book}_"))
    voice = a.voice or os.environ.get("MINIMAX_VOICE_ID", "Chinese (Mandarin)_Warm_Girl")
    wav = work / "title.wav"
    tts(a.title, voice, wav)
    adur = probe_dur(wav)
    cover = min(max(a.seconds, 1.0), 2.6)
    # if TTS is longer than the cover, extend just enough (max 2.6s)
    cover = max(cover, min(2.6, adur + 0.15))
    png = work / "cover.png"
    cover_png(a.title, a.subtitle, png)
    clip = work / "cover.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "24", "-t", str(cover),
         "-i", str(png), "-i", str(wav),
         "-filter_complex", f"[1:a]volume=3.8,apad=pad_dur={cover},atrim=0:{cover},asetpts=N/SR/TB[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-ar", "48000", "-ac", "2",
         "-b:a", "192k", "-shortest", str(clip)])
    # concat filter: re-encode both (CRF 18) so AAC timestamps/profile match
    run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-i", str(movie),
         "-filter_complex", "[0:v]fps=24,format=yuv420p[v0];[1:v]fps=24,format=yuv420p[v1];"
                            "[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-r", "24", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
         "-movflags", "+faststart", str(out)])
    print(json.dumps({"out": str(out), "cover_seconds": round(cover, 3),
                      "tts_seconds": round(adur, 3), "movie": str(movie)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())