"""调用现成本地 ASR HTTP 服务（默认 127.0.0.1:9881，faster-whisper large-v3）。"""
from __future__ import annotations

import base64
import logging
import time
from typing import Optional

import httpx

from ..config import Settings
from ..utils import detect_lang, pcm_duration_ms, wav_bytes
from .base import ASRProvider, ASRResult

log = logging.getLogger(__name__)


class HttpASR(ASRProvider):
    name = "http-asr"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=4.0))
        self.requests = 0
        self.failures = 0
        self.last_infer_ms: float | None = None

    async def transcribe(
        self,
        audio: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        partial: bool = False,
        initial_prompt: Optional[str] = None,
    ) -> ASRResult:
        t0 = time.perf_counter()
        try:
            payload = {
                "audio_b64": base64.b64encode(audio).decode("ascii"),
                "format": "wav",
            }
            resp = await self._client.post(self.settings.asr_http_url, json=payload)
            resp.raise_for_status()
            text = str(resp.json().get("text") or "").strip()
            self.requests += 1
            self.last_infer_ms = (time.perf_counter() - t0) * 1000
            return ASRResult(text, language or detect_lang(text), pcm_duration_ms(audio, sample_rate), partial)
        except Exception:
            self.failures += 1
            raise

    async def warmup(self) -> None:
        try:
            await self.transcribe(wav_bytes(b"\x00\x00" * 4800, 16000), 16000)
        except Exception as exc:  # noqa: BLE001
            log.warning("HTTP ASR warmup failed: %s", exc)

    async def close(self) -> None:
        await self._client.aclose()

    def status(self) -> dict:
        return {
            "provider": self.name,
            "url": self.settings.asr_http_url,
            "requests": self.requests,
            "failures": self.failures,
            "last_infer_ms": round(self.last_infer_ms or 0, 1),
        }
