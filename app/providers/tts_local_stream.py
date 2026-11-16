"""本地 TTS 客户端：优先 /tts_stream（CosyVoice3 真流式 / IndexTTS 整句流式），
失败时回落到已有的 9886 批量 shim。"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator, Optional

import httpx

from ..config import Settings
from ..utils import strip_wav_header
from .base import TTSProvider

log = logging.getLogger(__name__)


class TTSUnavailable(RuntimeError):
    pass


class LocalStreamTTS(TTSProvider):
    name = "local-tts"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sample_rate = settings.tts_sample_rate
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=4.0, read=settings.tts_timeout_s, write=15.0, pool=5.0),
            headers={"Accept": "application/x-pcm"},
        )
        self.first_chunk_ms: float | None = None
        self.requests = 0
        self.failures = 0
        self.last_engine = ""

    def _payload(self, text: str, language: Optional[str]) -> dict:
        return {
            "text": text,
            "text_lang": language or "zh",
            "lang": language or "zh",
            "ref_audio_path": self.settings.tts_local_ref_audio,
            "prompt_text": self.settings.tts_local_prompt_text,
            "speed_factor": self.settings.tts_local_speed,
            "streaming_mode": True,
            "media_type": "pcm",
        }

    async def _stream_http(self) -> AsyncIterator[bytes]:
        url = self.settings.tts_local_url.rstrip("/") + "/tts_stream"
        async with self._client.stream("POST", url, json=self._payload, headers={"Accept": "application/x-pcm"}) as resp:
            if resp.status_code != 200:
                body = (await resp.aread())[:200]
                raise TTSUnavailable(f"local tts {resp.status_code}: {body!r}")
            rate = resp.headers.get("X-Sample-Rate") or resp.headers.get("x-sample-rate")
            if rate and rate.isdigit():
                self.sample_rate = int(rate)
            self.last_engine = resp.headers.get("X-Engine", "local")
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                if chunk.startswith(b"RIFF"):
                    chunk, rate2 = strip_wav_header(chunk)
                    if rate2:
                        self.sample_rate = rate2
                if chunk:
                    yield chunk

    async def _stream_batch(self, text: str, language: Optional[str]) -> AsyncIterator[bytes]:
        url = self.settings.tts_local_batch_url
        payload = {
            "text": text,
            "text_lang": language or "zh",
            "ref_audio_path": self.settings.tts_local_ref_audio,
            "prompt_text": self.settings.tts_local_prompt_text,
            "prompt_lang": language or "zh",
            "speed_factor": self.settings.tts_local_speed,
            "streaming_mode": False,
            "media_type": "wav",
        }
        resp = await self._client.post(url, json=payload)
        if resp.status_code != 200:
            raise TTSUnavailable(f"batch tts {resp.status_code}: {resp.text[:200]}")
        data, rate = strip_wav_header(resp.content)
        if rate:
            self.sample_rate = rate
        self.last_engine = "local-batch"
        yield data

    async def stream(
        self,
        text: str,
        language: Optional[str] = None,
        *,
        seq: int = 0,
    ) -> AsyncIterator[bytes]:
        if not (text or "").strip():
            return
        t0 = time.perf_counter()
        self.requests += 1
        first = True
        try:
            async for chunk in self._stream_http():
                if first:
                    self.first_chunk_ms = (time.perf_counter() - t0) * 1000
                    first = False
                yield chunk
        except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout, TTSUnavailable) as exc:
            log.warning("stream tts failed (%s), falling back to batch", exc)
            try:
                async for chunk in self._stream_batch(text, language):
                    if first:
                        self.first_chunk_ms = (time.perf_counter() - t0) * 1000
                        first = False
                    yield chunk
            except Exception as exc2:  # noqa: BLE001
                self.failures += 1
                raise TTSUnavailable(str(exc2)) from exc2

    async def warmup(self) -> None:
        try:
            samples = 0
            t0 = time.perf_counter()
            async for chunk in self.stream("你好。", "zh"):
                samples += len(chunk)
            log.info(
                "TTS warmup %.0f ms, %.0f ms audio, engine=%s",
                (time.perf_counter() - t0) * 1000,
                samples / 2 / max(1, self.sample_rate) * 1000,
                self.last_engine,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("TTS warmup failed: %s", exc)

    async def close(self) -> None:
        await self._client.aclose()

    def status(self) -> dict:
        return {
            "provider": self.name,
            "url": self.settings.tts_local_url,
            "batch_url": self.settings.tts_local_batch_url,
            "sample_rate": self.sample_rate,
            "engine": self.last_engine,
            "first_chunk_ms": round(self.first_chunk_ms or 0, 1),
            "requests": self.requests,
            "failures": self.failures,
        }
