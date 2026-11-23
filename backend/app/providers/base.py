"""Provider 抽象：ASR / TTS / LLM 都可替换（本地 ↔ MiniMax ↔ mock）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Optional


@dataclass
class ASRResult:
    text: str
    lang: str = "zh"
    duration_ms: float = 0.0
    partial: bool = False


class ASRProvider(ABC):
    name = "base"

    async def warmup(self) -> None:  # pragma: no cover - optional
        return None

    async def close(self) -> None:  # pragma: no cover - optional
        return None

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        partial: bool = False,
        initial_prompt: Optional[str] = None,
    ) -> ASRResult:
        raise NotImplementedError

    async def transcribe_stream(
        self,
        audio: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        initial_prompt: Optional[str] = None,
    ) -> AsyncIterator[ASRResult]:
        """返回累计的识别内容，最后一项为定稿。"""
        yield await self.transcribe(audio, sample_rate, language=language, initial_prompt=initial_prompt)


class TTSProvider(ABC):
    name = "base"
    sample_rate = 24000

    async def warmup(self) -> None:  # pragma: no cover - optional
        return None

    async def close(self) -> None:  # pragma: no cover - optional
        return None

    @abstractmethod
    def stream(
        self,
        text: str,
        language: Optional[str] = None,
        *,
        seq: int = 0,
    ) -> AsyncIterator[bytes]:
        """逐块产出 PCM16LE 单声道音频。"""
        raise NotImplementedError

    async def synthesize(self, text: str, language: Optional[str] = None) -> bytes:
        chunks: list[bytes] = []
        async for chunk in self.stream(text, language):
            chunks.append(chunk)
        return b"".join(chunks)


class LLMProvider(ABC):
    name = "base"

    async def warmup(self) -> None:  # pragma: no cover - optional
        return None

    async def close(self) -> None:  # pragma: no cover - optional
        return None

    @abstractmethod
    def stream(self, messages: list[dict], context: Optional[dict] = None) -> AsyncIterator[str]:
        raise NotImplementedError
