# -*- coding: utf-8 -*-
"""Shared helpers for picturebook-one-take: env, Ark HTTP, ffmpeg, ASR, text."""
from __future__ import annotations

import base64
import difflib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARK_DEFAULT = "https://ark.cn-beijing.volces.com/api/v3"
DEEPSEEK_DEFAULT = "https://api.deepseek.com"
SEEDREAM_MODEL = "doubao-seedream-5-5-261028"
SEEDANCE_MODEL = "doubao-seedance-2-5-flash-261115"
PRICE_IMAGE_CNY = 0.12
# Ark 2.5 video is token-billed. These are upper-bound estimates used only for the
# local budget guard; the real bill is the account's token price.
TOKEN_CNY_PER_M_TEXT = 46.0
TOKEN_CNY_PER_M_VIDEO = 28.0


def load_env(path: Path | None = None) -> dict:
    env: dict[str, str] = {}
    p = path or ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            env[k.strip()] = v.strip()
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return env


ENV = load_env()
ARK = (ENV.get("ARK_BASE_URL") or ARK_DEFAULT).rstrip("/")
ARK_KEY = ENV.get("ARK_API_KEY", "")
DEEPSEEK = (ENV.get("DEEPSEEK_BASE_URL") or DEEPSEEK_DEFAULT).rstrip("/")
DEEPSEEK_KEY = ENV.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = ENV.get("DEEPSEEK_MODEL") or "deepseek-flash"
MINIMAX_HOST = (ENV.get("MINIMAX_HOST") or "https://api.minimaxi.com").rstrip("/")
MINIMAX_KEY = ENV.get("MINIMAX_API_KEY", "")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def jread(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jwrite(path: str | Path, obj) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def http_json(method: str, url: str, payload: dict | None = None, key: str = "",
              timeout: int = 120, retries: int = 2) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=body, method=method,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw.decode("utf-8")) if raw else {})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:2000]
            last = f"HTTP {exc.code}: {detail}"
            if exc.code in (400, 401, 403, 404):
                break
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url} failed: {last}")


def ark_post(path: str, payload: dict, timeout: int = 120) -> tuple[int, dict]:
    return http_json("POST", ARK + path, payload, ARK_KEY, timeout=timeout)


def ark_get(path: str, timeout: int = 60) -> dict:
    _, data = http_json("GET", ARK + path, None, ARK_KEY, timeout=timeout)
    return data


def download(url: str, out: str | Path, timeout: int = 600) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "book2real-one-take/1.0"})
    tmp = p.with_suffix(p.suffix + ".part")
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    tmp.replace(p)
    return p


def data_url(path: str | Path) -> str:
    p = Path(path)
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def ffprobe(path: str | Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
           "-show_entries", "format=duration,size", "-of", "json", str(path)]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def ffmpeg(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True,
                          text=True, timeout=timeout)


def extract_frame(video: str | Path, t: float, out: str | Path) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                    "-frames:v", "1", str(p)], check=True)
    return p


def last_frame(video: str | Path, out: str | Path) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.05", "-i", str(video),
                    "-frames:v", "1", str(p)], check=True)
    return p


def first_frame(video: str | Path, out: str | Path) -> Path:
    return extract_frame(video, 0.0, out)


def audio_peak_db(video: str | Path) -> float | None:
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-af", "volumedetect",
         "-f", "null", os.devnull], capture_output=True, text=True, errors="replace")
    m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", res.stderr)
    return float(m.group(1)) if m else None


def normalize(text: str) -> str:
    text = (text or "").replace("“", "").replace("”", "").replace("\"", "")
    text = text.replace("‘", "").replace("’", "").replace("'", "")
    text = re.sub(r"[\s，。！？、；：…—,.!?;:~·（）()\[\]【】《》<>{}]", "", text)
    return text.strip()


def overlap(a: str, b: str) -> float:
    """Character-level similarity of two normalized strings (0..1)."""
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def contains_approx(source: str, line: str, threshold: float = 0.96) -> bool:
    s, l = normalize(source), normalize(line)
    if not l:
        return False
    if l in s:
        return True
    # allow one stray character from OCR/typo
    n = len(l)
    for i in range(0, max(1, len(s) - n + 1)):
        if overlap(s[i:i + n], l) >= threshold:
            return True
    return False


def asr(path: str | Path, language: str = "zh") -> dict:
    """MiniMax ASR (asr-1.0). Returns {text, duration}."""
    if not MINIMAX_KEY:
        raise RuntimeError("MINIMAX_API_KEY missing; ASR gate cannot run")
    wav = Path(path).with_suffix(".asr.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1",
                    "-ar", "16000", str(wav)], check=True)
    boundary = "----one" + uuid.uuid4().hex
    body = bytearray()

    def field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())

    field("model", ENV.get("MINIMAX_ASR_MODEL", "asr-1.0"))
    field("response_format", "json")
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode())
    body.extend(wav.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        MINIMAX_HOST + "/v1/speech_to_text", data=bytes(body), method="POST",
        headers={"Authorization": f"Bearer {MINIMAX_KEY}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}",
                 "language": language})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return {"text": str(data.get("text") or ""), "duration": data.get("duration"), "raw": data}


def ledger_append(out: Path, row: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    f = out / "cost.csv"
    new = not f.exists()
    with f.open("a", encoding="utf-8") as fh:
        if new:
            fh.write("ts,chunk,task_id,model,resolution,duration_sec,tokens,est_cny,note\n")
        def clean(v):
            return str(v).replace(",", ";").replace("\n", " ").replace("\r", " ")
        fh.write(",".join(clean(row.get(k, "")) for k in
                          ("ts", "chunk", "task_id", "model", "resolution",
                           "duration_sec", "tokens", "est_cny", "note")) + "\n")


def est_video_cny(tokens: int | None, has_video_input: bool = False) -> float:
    if not tokens:
        return 0.0
    rate = TOKEN_CNY_PER_M_VIDEO if has_video_input else TOKEN_CNY_PER_M_TEXT
    return round(tokens * rate / 1_000_000.0, 3)
