"""MiniMax TTS 流式客户端（SSE + hex PCM 分片）。

11.30 官方 API 发放后，只需在 .env 里填 MINIMAX_API_KEY / MINIMAX_VOICE_ID，
把 TTS_PROVIDER 改成 minimax 即可；管线其余部分不变。
"""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Optional

import httpx

from ..config import Settings
from ..utils import cjk_ratio
from .base import TTSProvider

log = logging.getLogger(__name__)


class TTSConfigError(RuntimeError):
    pass


class MiniMaxTTS(TTSProvider):
    name = "minimax-tts"

    def __init__(self, settings: Settings) -> None:
        if not settings.minimax_api_key:
            raise TTSConfigError("MINIMAX_API_KEY is empty")
        if not settings.minimax_voice_id:
            raise TTSConfigError("MINIMAX_VOICE_ID is empty")
        self.settings = settings
        self.sample_rate = settings.minimax_sample_rate
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=4.0, read=settings.tts_timeout_s, write=15.0, pool=5.0),
            headers={
                "Authorization": f"Bearer {settings.minimax_api_key}",
                "Content-Type": "application/json",
            },
        )
        self.first_chunk_ms: float | None = None
        self.requests = 0
        self.failures = 0

    @property
    def endpoint(self) -> str:
        url = self.settings.minimax_host.rstrip("/") + "/v1/t2a_v2"
        if self.settings.minimax_group_id:
            url += f"?GroupId={self.settings.minimax_group_id}"
        return url

    def _language_boost(self, language: Optional[str]) -> str:
        if self.settings.minimax_language_boost and self.settings.minimax_language_boost != "auto":
            return self.settings.minimax_language_boost
        if language == "en":
            return "English"
        if language == "zh":
            return "Chinese"
        return "auto"

    def _voice_for(self, text: str, language: Optional[str]) -> str:
        """音色跟随回答文本：中文回答用中文音色，英文回答用英文音色。"""
        if cjk_ratio(text) > 0.1:
            return self.settings.minimax_voice_id
        return self.settings.minimax_voice_id_en or self.settings.minimax_voice_id

    async def stream(
        self,
        text: str,
        language: Optional[str] = None,
        *,
        seq: int = 0,
    ) -> AsyncIterator[bytes]:
        text = (text or "").strip()
        if not text:
            return
        payload = {
            "model": self.settings.minimax_tts_model,
            "text": text,
            "stream": True,
            "stream_options": {"exclude_aggregated_audio": True},
            "voice_setting": {
                "voice_id": self._voice_for(text, language),
                "speed": self.settings.minimax_speed,
                "vol": self.settings.minimax_vol,
                "pitch": self.settings.minimax_pitch,
            },
            "audio_setting": {
                "sample_rate": self.sample_rate,
                "bitrate": 128000,
                "format": "pcm",
                "channel": 1,
            },
            "language_boost": self._language_boost(language),
        }
        if self.settings.minimax_emotion:
            payload["voice_setting"]["emotion"] = self.settings.minimax_emotion
        t0 = time.perf_counter()
        first = True
        self.requests += 1
        try:
            async with self._client.stream("POST", self.endpoint, json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread())[:300]
                    raise RuntimeError(f"minimax tts {resp.status_code}: {body!r}")
                async for line in resp.aiter_lines():
                    line = (line or "").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    base = obj.get("base_resp") or {}
                    if base.get("status_code") not in (None, 0):
                        raise RuntimeError(f"minimax tts error {base.get('status_code')}: {base.get('status_msg')}")
                    data = obj.get("data") or {}
                    extra = obj.get("extra_info") or {}
                    rate = extra.get("sample_rate")
                    if isinstance(rate, int) and rate > 0:
                        self.sample_rate = rate
                    audio_hex = data.get("audio")
                    if audio_hex:
                        try:
                            chunk = bytes.fromhex(audio_hex)
                        except ValueError:
                            continue
                        if chunk:
                            if first:
                                self.first_chunk_ms = (time.perf_counter() - t0) * 1000
                                first = False
                            yield chunk
                    if data.get("status") == 2:
                        break
        except Exception:
            self.failures += 1
            raise

    async def warmup(self) -> None:
        try:
            t0 = time.perf_counter()
            n = 0
            async for chunk in self.stream("你好。", "zh"):
                n += len(chunk)
            log.info("MiniMax TTS warmup %.0f ms, %.0f ms audio", (time.perf_counter() - t0) * 1000, n / 2 / max(1, self.sample_rate) * 1000)
        except Exception as exc:  # noqa: BLE001
            log.warning("MiniMax TTS warmup failed: %s", exc)

    async def close(self) -> None:
        await self._client.aclose()

    def status(self) -> dict:
        return {
            "provider": self.name,
            "endpoint": self.endpoint,
            "model": self.settings.minimax_tts_model,
            "voice_id": self.settings.minimax_voice_id,
            "voice_id_en": self.settings.minimax_voice_id_en,
            "sample_rate": self.sample_rate,
            "first_chunk_ms": round(self.first_chunk_ms or 0, 1),
            "requests": self.requests,
            "failures": self.failures,
        }
