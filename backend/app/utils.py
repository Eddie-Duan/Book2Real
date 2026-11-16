"""通用小工具：文本归一化、语言判断、PCM/WAV 处理、时间。"""
from __future__ import annotations

import difflib
import io
import json
import math
import re
import time
import unicodedata
import wave
from typing import Any

import numpy as np

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\s\u3000!-/:-@\[-`{-~\u3001-\u303f\uff01-\uff5e]+")
_MD_RE = re.compile(r"[*_#`>]+")


def now() -> float:
    return time.time()


def now_ms() -> float:
    return time.time() * 1000.0


def perf_ms() -> float:
    return time.perf_counter() * 1000.0


def normalize_text(text: str) -> str:
    """用于比较/检索的归一化：NFKC + 小写 + 去标点空白。"""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return _PUNCT_RE.sub("", text)


def normalize_for_tokens(text: str) -> str:
    """分词用归一化：保留英文词间空格，标点变空格，CJK 字符连续保留。"""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)


def detect_lang(text: str, default: str = "zh") -> str:
    text = text or ""
    cjk = len(_CJK_RE.findall(text))
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if cjk == 0 and latin == 0:
        return default
    return "zh" if cjk >= max(1, latin * 0.35) else "en"


def cjk_ratio(text: str) -> float:
    text = text or ""
    if not text:
        return 0.0
    return len(_CJK_RE.findall(text)) / len(text)


def similarity(a: str, b: str) -> float:
    a, b = normalize_text(a), normalize_text(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def clean_for_tts(text: str) -> str:
    """去掉 markdown/emoji/多余空白，保留中英文标点与数字。"""
    text = _MD_RE.sub("", text or "")
    text = "".join(
        ch for ch in text
        if ch.isprintable() and not (0x1F000 <= ord(ch) <= 0x1FAFF) and ord(ch) != 0xFE0F
    )
    text = text.replace("…", "。").replace("——", "，").replace("--", "，")
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def pcm16_to_float32(pcm: bytes) -> np.ndarray:
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    data = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    return data / 32768.0


def float32_to_pcm16(samples: np.ndarray | list[float]) -> bytes:
    arr = np.asarray(samples, dtype=np.float32)
    if arr.size == 0:
        return b""
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()


def pcm_duration_ms(pcm: bytes, sample_rate: int) -> float:
    if sample_rate <= 0:
        return 0.0
    return len(pcm) / 2.0 / sample_rate * 1000.0


def wav_bytes(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def strip_wav_header(data: bytes) -> tuple[bytes, int | None]:
    """若为 WAV 则返回 (pcm, sample_rate)，否则原样返回。"""
    if not data.startswith(b"RIFF") or data[8:12] != b"WAVE":
        return data, None
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                return data, None
            return wf.readframes(wf.getnframes()), wf.getframerate()
    except Exception:
        return data, None


def read_wav_file(path: str) -> tuple[bytes, int]:
    with wave.open(path, "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError("only PCM16 WAV supported")
        channels = wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
        if channels > 1:
            arr = np.frombuffer(frames, dtype="<i2").reshape(-1, channels)
            frames = arr[:, 0].astype("<i2").tobytes()
        return frames, wf.getframerate()


def rms_normalized(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    samples = pcm16_to_float32(pcm)
    if samples.size == 0:
        return 0.0
    return float(math.sqrt(float(np.mean(samples * samples)) + 1e-12))


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate or not pcm:
        return pcm
    samples = pcm16_to_float32(pcm)
    if samples.size == 0:
        return b""
    n_dst = max(1, int(round(samples.size * dst_rate / src_rate)))
    x_old = np.linspace(0.0, 1.0, samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_dst, endpoint=False)
    return float32_to_pcm16(np.interp(x_new, x_old, samples))


def decode_audio_bytes(data: bytes, sample_rate: int = 16000) -> tuple[bytes, int]:
    """把任意常见音频（wav/mp3/m4a/webm）解码成 mono PCM16。优先 wave，失败用 PyAV。"""
    pcm, rate = strip_wav_header(data)
    if rate:
        return resample_pcm16(pcm, rate, sample_rate), sample_rate
    try:
        import av

        with av.open(io.BytesIO(data)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise ValueError("no audio stream")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
            out = bytearray()
            for frame in container.decode(stream):
                for resampled in resampler.resample(frame):
                    out.extend(bytes(resampled.planes[0]))
            for resampled in resampler.resample(None):
                out.extend(bytes(resampled.planes[0]))
            return bytes(out), sample_rate
    except Exception as exc:
        raise ValueError(f"cannot decode audio: {exc}") from exc



def short_hash(*parts: str) -> str:
    import hashlib

    h = hashlib.sha1()
    for part in parts:
        h.update((part or "").encode("utf-8", "ignore"))
        h.update(b"\x00")
    return h.hexdigest()[:16]
