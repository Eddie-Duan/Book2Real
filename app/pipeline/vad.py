"""轻量能量 VAD：20ms 帧、自适应噪声底、260ms 静音判停。"""
from __future__ import annotations

from dataclasses import dataclass

from ..utils import pcm16_to_float32, rms_normalized


@dataclass
class VADEvent:
    kind: str  # speech_start | speech_end
    time_ms: float
    speech_ms: float = 0.0
    last_speech_ms: float = 0.0


class EnergyVAD:
    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        silence_ms: int = 260,
        min_speech_ms: int = 100,
        energy_ratio: float = 3.2,
        min_rms: float = 0.006,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_bytes = int(sample_rate * frame_ms / 1000) * 2
        self.silence_ms = silence_ms
        self.min_speech_ms = min_speech_ms
        self.energy_ratio = energy_ratio
        self.min_rms = min_rms
        self._buf = bytearray()
        self.reset()

    def reset(self) -> None:
        self.started = False
        self.speech_ms = 0.0
        self.frames = 0
        self.noise = 0.0
        self._speech_run = 0.0
        self._silence_run = 0.0
        self._speech_total = 0.0
        self._elapsed = 0.0
        self._last_speech_elapsed = 0.0

    @property
    def threshold(self) -> float:
        if self.frames < 12:
            return max(self.min_rms, 0.012)
        return max(self.min_rms, min(self.noise * self.energy_ratio, 0.025))

    def push(self, pcm: bytes) -> list[VADEvent]:
        events: list[VADEvent] = []
        if pcm:
            self._buf.extend(pcm)
        while len(self._buf) >= self.frame_bytes:
            frame = bytes(self._buf[: self.frame_bytes])
            del self._buf[: self.frame_bytes]
            self._elapsed += self.frame_ms
            rms = rms_normalized(frame)
            if self.frames < 8:
                self.noise = min(0.01, rms if self.noise <= 0 else self.noise * 0.7 + rms * 0.3)
            self.frames += 1
            speech = rms >= self.threshold and rms >= self.min_rms
            if not self.started:
                if speech:
                    self._speech_run += self.frame_ms
                    if self._speech_run >= self.min_speech_ms:
                        self.started = True
                        self._speech_total = self._speech_run
                        self._silence_run = 0.0
                        events.append(VADEvent("speech_start", self._elapsed, self._speech_total))
                else:
                    self._speech_run = 0.0
                    self.noise = self.noise * 0.96 + rms * 0.04
            else:
                if speech:
                    self._speech_total += self.frame_ms
                    self._silence_run = 0.0
                    self._last_speech_elapsed = self._elapsed
                else:
                    self._silence_run += self.frame_ms
                    if self._silence_run >= self.silence_ms:
                        self.started = False
                        events.append(
                            VADEvent("speech_end", self._elapsed, self._speech_total, self._last_speech_elapsed)
                        )
                        self._speech_run = 0.0
                        self._silence_run = 0.0
                        self._speech_total = 0.0
        return events

    def flush(self) -> list[VADEvent]:
        if self.started and self._speech_total >= self.min_speech_ms:
            self.started = False
            event = VADEvent("speech_end", self._elapsed, self._speech_total, self._last_speech_elapsed)
            self._speech_total = 0.0
            return [event]
        return []


def trim_speech(pcm: bytes, sample_rate: int = 16000, pad_ms: int = 120) -> tuple[bytes, float, float]:
    """返回去掉首尾静音后的 PCM 与起止毫秒；没检测到语音则原样返回。"""
    frame_bytes = max(2, int(sample_rate * 0.02) * 2)
    frames = []
    for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        frames.append(rms_normalized(pcm[i : i + frame_bytes]))
    if not frames:
        return pcm, 0.0, len(pcm) / 2 / sample_rate * 1000
    noise = sorted(frames)[max(0, len(frames) // 10)]
    threshold = max(0.006, noise * 3.2)
    speech_idx = [i for i, r in enumerate(frames) if r >= threshold]
    if not speech_idx:
        return pcm, 0.0, len(pcm) / 2 / sample_rate * 1000
    total_ms = len(pcm) / 2 / sample_rate * 1000
    start_ms = max(0.0, speech_idx[0] * 20 - pad_ms)
    end_ms = min(total_ms, (speech_idx[-1] + 1) * 20 + pad_ms)
    start_b = int(start_ms * sample_rate / 1000) * 2
    end_b = int(end_ms * sample_rate / 1000) * 2
    return pcm[start_b:end_b], start_ms, end_ms
