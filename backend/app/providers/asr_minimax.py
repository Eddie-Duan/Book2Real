"""MiniMax Speech-to-Text（asr-1.0）文件上传及 SSE 识别输出。

官方：POST {host}/v1/speech_to_text
  multipart: model=asr-1.0, file=音频文件
  header: Authorization: Bearer <key>；可选 language: zh/en/...
  返回: {text, duration, trace_id}
"""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Optional

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
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=45.0),
            headers={"Authorization": f"Bearer {settings.minimax_api_key}"},
        )
        self.requests = 0
        self.failures = 0
        self.last_infer_ms: float | None = None

    @property
    def endpoint(self) -> str:
        return self.settings.minimax_host.rstrip("/") + "/v1/speech_to_text"

    def _request(self, audio: bytes, rate: int, language: Optional[str], *, stream: bool = False) -> dict:
        if not (audio.startswith(b"RIFF") and audio[8:12] == b"WAVE"):
            audio = wav_bytes(audio, rate)
        data = {"model": self.model, "response_format": "json"}
        if stream:
            data["stream"] = "true"
        return {
            "data": data,
            "files": {"file": ("audio.wav", audio, "audio/wav")},
            "headers": {"language": language} if language else {},
        }

    @staticmethod
    def _check_event(obj: dict) -> None:
        error = obj.get("error")
        base = obj.get("base_resp") or {}
        if error or obj.get("type") == "error":
            detail = error.get("type") if isinstance(error, dict) else error
            raise RuntimeError(f"MiniMax ASR error: {detail or 'unknown'}")
        if base.get("status_code") not in (None, 0):
            raise RuntimeError(f"MiniMax ASR error {base['status_code']}: {base.get('status_msg', '')}")

    def _result(
        self, obj: dict, lang: Optional[str], fallback_ms: float, partial: bool = False,
    ) -> ASRResult:
        self._check_event(obj)
        text = str(obj.get("text") or (obj.get("data") or {}).get("text") or "").strip()
        duration = obj.get("duration")
        return ASRResult(text, lang or detect_lang(text), fallback_ms if duration is None else float(duration) * 1000, partial)

    async def transcribe(
        self,
        audio: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        partial: bool = False,
        initial_prompt: Optional[str] = None,
    ) -> ASRResult:
        lang = language or self.settings.minimax_asr_language or None
        t0 = time.perf_counter()
        self.requests += 1
        try:
            resp = await self._client.post(self.endpoint, **self._request(audio, sample_rate, lang))
            resp.raise_for_status()
            self.last_infer_ms = (time.perf_counter() - t0) * 1000
            return self._result(resp.json(), lang, pcm_duration_ms(audio, sample_rate), partial)
        except Exception:
            self.failures += 1
            raise

    async def transcribe_stream(
        self,
        audio: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        initial_prompt: Optional[str] = None,
    ) -> AsyncIterator[ASRResult]:
        lang = language or self.settings.minimax_asr_language or None
        duration = pcm_duration_ms(audio, sample_rate)
        t0 = time.perf_counter()
        self.requests += 1
        try:
            async with self._client.stream("POST", self.endpoint, **self._request(audio, sample_rate, lang, stream=True)) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "").lower()
                if "application/json" in content_type:
                    await resp.aread()
                    result = self._result(resp.json(), lang, duration)
                else:
                    chunks: list[str] = []
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload:
                            continue
                        obj = json.loads(payload)
                        self._check_event(obj)
                        delta = obj.get("delta")
                        if delta:
                            chunks.append(delta)
                        text = "".join(chunks)
                        if obj.get("finish") is True:
                            result = self._result({**obj, "text": text}, lang, duration)
                            break
                        if delta:
                            yield ASRResult(text, lang or detect_lang(text), duration, partial=True)
                    else:
                        raise RuntimeError("MiniMax ASR ended without a final transcription")
            self.last_infer_ms = (time.perf_counter() - t0) * 1000
            yield result
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
