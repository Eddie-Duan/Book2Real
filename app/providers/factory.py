"""Provider 工厂：按配置选择实现，配置不全时安全回落。"""
from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from ..config import Settings
from .base import ASRProvider, ASRResult, LLMProvider, TTSProvider
from .asr_http import HttpASR
from .asr_local import LocalWhisperASR
from .llm import ExtractiveLLM, MockLLM, OpenAICompatLLM
from .tts_local_stream import LocalStreamTTS
from .tts_minimax import MiniMaxTTS, TTSConfigError

log = logging.getLogger(__name__)


class MockASR(ASRProvider):
    name = "mock-asr"

    def __init__(self, text: str = "他们在搭什么？") -> None:
        self.text = text

    async def transcribe(self, audio: bytes, sample_rate: int = 16000, language: Optional[str] = None, partial: bool = False, initial_prompt: Optional[str] = None) -> ASRResult:
        return ASRResult(self.text, language or "zh", 1000.0, partial)


class NullTTS(TTSProvider):
    name = "null-tts"
    sample_rate = 24000

    async def stream(self, text: str, language: Optional[str] = None, *, seq: int = 0) -> AsyncIterator[bytes]:
        if False:  # pragma: no cover
            yield b""


def build_asr(settings: Settings) -> ASRProvider:
    provider = settings.asr_provider
    if provider == "http":
        return HttpASR(settings)
    if provider == "mock":
        return MockASR()
    if provider != "local":
        log.warning("unknown ASR_PROVIDER=%s, using local", provider)
    return LocalWhisperASR(settings)


def build_tts(settings: Settings) -> TTSProvider:
    provider = settings.tts_provider
    if provider in ("sapi", "auto"):
        try:
            from .tts_sapi import SapiTTS

            return SapiTTS(settings)
        except Exception as exc:  # noqa: BLE001
            if provider == "sapi":
                log.warning("SAPI unavailable (%s), falling back to local", exc)
            return LocalStreamTTS(settings)
    if provider == "minimax":
        try:
            return MiniMaxTTS(settings)
        except TTSConfigError as exc:
            log.warning("MiniMax TTS not configured (%s), falling back to local", exc)
            return LocalStreamTTS(settings)
    if provider in ("none", "off"):
        return NullTTS()
    if provider != "local":
        log.warning("unknown TTS_PROVIDER=%s, using local", provider)
    return LocalStreamTTS(settings)


def build_llm(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider
    if provider in ("openai", "deepseek", "openai-compat"):
        try:
            return OpenAICompatLLM(settings)
        except ValueError as exc:
            log.warning("LLM not configured (%s), using extractive fallback", exc)
            return ExtractiveLLM()
    if provider == "mock":
        return MockLLM()
    return ExtractiveLLM()
