"""Windows SAPI5 本地 TTS：CPU 合成，短句约 10-80ms，作为 2s 首音的本地兜底。

最终比赛用 MiniMax 云端音色；SAPI 只用于本地联调与断网兜底。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
import time
import wave
from typing import AsyncIterator, Optional

from ..config import Settings
from ..utils import cjk_ratio
from .base import TTSProvider

log = logging.getLogger(__name__)


class SapiTTS(TTSProvider):
    name = "sapi"

    def __init__(self, settings: Settings) -> None:
        try:
            import pythoncom  # noqa: F401
            import win32com.client  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"SAPI unavailable: {exc}") from exc
        self.settings = settings
        self.sample_rate = 22050
        self._lock = threading.Lock()
        self.requests = 0
        self.failures = 0
        self.last_synth_ms: float | None = None
        self._voices: dict[str, str] = {
            "zh": settings.sapi_voice_zh or "Huihui",
            "en": settings.sapi_voice_en or "Zira",
        }

    def _synth_sync(self, text: str, language: str) -> tuple[bytes, int]:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        path = ""
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voices = voice.GetVoices()
            wanted = self._voices.get(language, self._voices["zh"]).lower()
            for i in range(voices.Count):
                desc = voices.Item(i).GetDescription()
                if wanted in desc.lower():
                    voice.Voice = voices.Item(i)
                    break
            else:
                # 没有对应语种音色时退回第一个
                voice.Voice = voices.Item(0)
            voice.Rate = int(self.settings.sapi_rate)
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="sapi_")
            os.close(fd)
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Open(path, 3, False)  # SSFMCreateForWrite
            voice.AudioOutputStream = stream
            voice.Speak(text)
            stream.Close()
            with wave.open(path, "rb") as wf:
                rate = wf.getframerate()
                data = wf.readframes(wf.getnframes())
                if wf.getnchannels() > 1:
                    import numpy as np

                    arr = np.frombuffer(data, dtype="<i2").reshape(-1, wf.getnchannels())
                    data = arr[:, 0].astype("<i2").tobytes()
            return data, rate
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    async def _synth(self, text: str, language: str) -> tuple[bytes, int]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._synth_sync, text, language)

    async def stream(self, text: str, language: Optional[str] = None, *, seq: int = 0) -> AsyncIterator[bytes]:
        text = (text or "").strip()
        if not text:
            return
        # 音色跟随文本，而不是提问语言：英文提问也可能得到中文回答
        lang = "zh" if cjk_ratio(text) > 0.1 else (language or "zh")
        t0 = time.perf_counter()
        try:
            data, rate = await self._synth(text, lang)
            if not data:
                raise RuntimeError("SAPI produced empty audio")
            self.requests += 1
            self.sample_rate = rate
            self.last_synth_ms = (time.perf_counter() - t0) * 1000
            # 100ms 一块，便于前端尽早起播
            step = max(2, int(rate * 0.1) * 2)
            for i in range(0, len(data), step):
                chunk = data[i : i + step]
                if chunk:
                    yield chunk
        except Exception:
            self.failures += 1
            raise

    async def warmup(self) -> None:
        try:
            t0 = time.perf_counter()
            await self._synth("你好。", "zh")
            await self._synth("Hello.", "en")
            log.info("SAPI warmup %.0f ms", (time.perf_counter() - t0) * 1000)
        except Exception as exc:  # noqa: BLE001
            log.warning("SAPI warmup failed: %s", exc)

    def status(self) -> dict:
        return {
            "provider": self.name,
            "voices": self._voices,
            "sample_rate": self.sample_rate,
            "last_synth_ms": round(self.last_synth_ms or 0, 1),
            "requests": self.requests,
            "failures": self.failures,
        }
