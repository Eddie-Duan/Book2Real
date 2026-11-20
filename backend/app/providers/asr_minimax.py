"""MiniMax Speech-to-Text（asr-1.0）客户端。

官方：POST {host}/v1/speech_to_text
  multipart: model=asr-1.0, file=音频文件
  header: Authorization: Bearer <key>；可选 language: zh/en/...
  返回: {text, duration, trace_id}
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from ..config import Settings
from ..utils import detect_lang, pcm_duration_ms, wav_bytes
from .base import ASRProvider, ASRResult

log = logging.getLogger(__name__)


class ASRConfigError(RuntimeError):
    pass


class MiniMaxASR(ASRProvider):
    name = "minimax-asr"

    def __init__(self, settings: Settings) -> None:
        if not settings.minimax_api_key:
            raise ASRConfigError("MINIMAX_API_KEY is empty")
        self.settings = settings
        self.model = settings.minimax_asr_model
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=60.0, pool=5.0),
            headers={"Authorization": f"Bearer {settings.minimax_api_key}"},
        )
        self.requests = 0
        self.failures = 0
        self.last_infer_ms: float | None = None

    @property
    def endpoint(self) -> str:
        return self.settings.minimax_host.rstrip("/") + "/v1/speech_to_text"

    async def transcribe(
        self,
        audio: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        partial: bool = False,
        initial_prompt: Optional[str] = None,
    ) -> ASRResult:
        # MiniMax 要求容器格式，不接受裸 PCM
        if not (audio.startswith(b"RIFF") and audio[8:12] == b"WAVE"):
            audio = wav_bytes(audio, sample_rate)
        lang = language or self.settings.minimax_asr_language or None
        headers = {}
        if lang:
            headers["language"] = lang
        data = {"model": self.model, "response_format": "json"}
        files = {"file": ("audio.wav", audio, "audio/wav")}
        import time

        t0 = time.perf_counter()
        try:
            resp = await self._client.post(self.endpoint, data=data, files=files, headers=headers)
            self.requests += 1
            self.last_infer_ms = (time.perf_counter() - t0) * 1000
            if resp.status_code != 200:
                body = resp.text[:500]
                raise RuntimeError(f"minimax asr {resp.status_code}: {body}")
            obj = resp.json()
            text = str(obj.get("text") or (obj.get("data") or {}).get("text") or "").strip()
            duration = float(obj.get("duration") or pcm_duration_ms(audio, sample_rate) / 1000.0)
            return ASRResult(text, lang or detect_lang(text), duration * 1000.0, partial)
        except Exception:
            self.failures += 1
            raise

    async def warmup(self) -> None:
        # 避免无意义地消耗额度；首个真实回合本身就是预热
        return None

    async def close(self) -> None:
        await self._client.aclose()

    def status(self) -> dict:
        return {
            "provider": self.name,
            "model": self.model,
            "endpoint": self.endpoint,
            "configured": bool(self.settings.minimax_api_key),
            "requests": self.requests,
            "failures": self.failures,
            "last_infer_ms": round(self.last_infer_ms or 0, 1),
        }
