from __future__ import annotations

import math

import numpy as np

from app.pipeline.vad import EnergyVAD, trim_speech


def _tone(seconds: float, rate: int = 16000, freq: float = 440.0, amp: float = 0.3) -> bytes:
    n = int(seconds * rate)
    t = np.arange(n) / rate
    data = (np.sin(2 * math.pi * freq * t) * amp * 32767).astype("<i2")
    return data.tobytes()


def _silence(seconds: float, rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(seconds * rate)


def test_vad_start_and_end():
    pcm = _silence(0.2) + _tone(0.5) + _silence(0.7)
    vad = EnergyVAD(sample_rate=16000, silence_ms=500, min_speech_ms=100)
    events = []
    frame = int(16000 * 0.02) * 2
    for i in range(0, len(pcm), frame):
        events.extend(vad.push(pcm[i : i + frame]))
    events.extend(vad.flush())
    kinds = [e.kind for e in events]
    assert "speech_start" in kinds
    assert "speech_end" in kinds
    end = [e for e in events if e.kind == "speech_end"][-1]
    assert end.last_speech_ms > 0
    assert end.time_ms - end.last_speech_ms >= 450


def test_trim_speech_removes_padding():
    pcm = _silence(0.3) + _tone(0.6) + _silence(0.5)
    trimmed, start, end = trim_speech(pcm, 16000, pad_ms=100)
    assert 100 <= start <= 400
    assert 700 <= end <= 1500
    assert 0.3 < len(trimmed) / 2 / 16000 < 1.2
