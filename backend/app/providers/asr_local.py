"""本地 faster-whisper ASR：进程内加载，支持整段与滚动部分识别。"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional

from ..config import Settings
from .base import ASRProvider, ASRResult
from ..utils import detect_lang, pcm16_to_float32, pcm_duration_ms, strip_wav_header

log = logging.getLogger(__name__)


class LocalWhisperASR(ASRProvider):
    name = "local-whisper"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._load_lock = asyncio.Lock()
        self._infer_lock = threading.Lock()
        self._loading = False
        self.last_load_ms: float | None = None
        self.last_infer_ms: float | None = None
        self.requests = 0
        self.failures = 0

    # ── 模型加载 ────────────────────────────────────────────────
    def _load_sync(self):
        from faster_whisper import WhisperModel

        device = self.settings.asr_device or "auto"
        if device == "auto":
            try:
                import ctranslate2

                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        compute = self.settings.asr_compute or ("float16" if device == "cuda" else "int8")
        log.info("loading faster-whisper model=%s device=%s compute=%s", self.settings.asr_model, device, compute)
        try:
            return WhisperModel(self.settings.asr_model, device=device, compute_type=compute)
        except Exception as exc:  # noqa: BLE001
            if device == "cuda":
                log.warning("CUDA load failed (%s), falling back to CPU int8", exc)
                return WhisperModel(self.settings.asr_model, device="cpu", compute_type="int8")
            raise

    async def _ensure_model(self):
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                self._loading = True
                t0 = time.perf_counter()
                try:
                    self._model = await asyncio.to_thread(self._load_sync)
                    self.last_load_ms = (time.perf_counter() - t0) * 1000
                    log.info("ASR model ready in %.0f ms", self.last_load_ms)
                finally:
                    self._loading = False
        return self._model

    # ── 推理 ────────────────────────────────────────────────────
    def _run_sync(self, samples, language: Optional[str], partial: bool, initial_prompt: Optional[str] = None):
        kwargs = {
            "beam_size": 1 if partial else 5,
            "task": "transcribe",
            "condition_on_previous_text": False,
            "without_timestamps": True,
            "language": language or None,
        }
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        if partial:
            kwargs["vad_filter"] = False
        else:
            kwargs["vad_filter"] = True
            kwargs["vad_parameters"] = {"min_silence_duration_ms": 300}
        with self._infer_lock:
            segments, info = self._model.transcribe(samples, **kwargs)
            texts = [seg.text.strip() for seg in segments]
        return "".join(texts).strip(), info

    async def transcribe(
        self,
        audio: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        partial: bool = False,
        initial_prompt: Optional[str] = None,
    ) -> ASRResult:
        await self._ensure_model()
        pcm, wav_rate = strip_wav_header(audio)
        if wav_rate:
            sample_rate = wav_rate
        samples = pcm16_to_float32(pcm)
        if samples.size == 0:
            return ASRResult("", language or "zh", 0.0, partial)
        lang = language or self.settings.asr_language or None
        t0 = time.perf_counter()
        try:
            text, info = await asyncio.to_thread(self._run_sync, samples, lang, partial, initial_prompt)
            self.requests += 1
            self.last_infer_ms = (time.perf_counter() - t0) * 1000
            detected = getattr(info, "language", None) or lang or detect_lang(text)
            return ASRResult(text, detected, pcm_duration_ms(pcm, sample_rate), partial)
        except Exception:
            self.failures += 1
            raise

    async def warmup(self) -> None:
        t0 = time.perf_counter()
        await self._ensure_model()
        # 0.3s 静音，触发一次完整解码路径
        await self.transcribe(b"\x00\x00" * 4800, 16000, language="zh", partial=False)
        log.info("ASR warmup %.0f ms", (time.perf_counter() - t0) * 1000)

    def status(self) -> dict:
        return {
            "provider": self.name,
            "model": self.settings.asr_model,
            "loaded": self._model is not None,
            "loading": self._loading,
            "device": self.settings.asr_device,
            "last_load_ms": round(self.last_load_ms or 0, 1),
            "last_infer_ms": round(self.last_infer_ms or 0, 1),
            "requests": self.requests,
            "failures": self.failures,
        }
