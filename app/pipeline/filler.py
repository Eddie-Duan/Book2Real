"""首音兜底填充：预热时预合成一句自然的口头垫音，1.1s 内没有真回答就先播它。"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import Settings
from ..providers.base import TTSProvider

log = logging.getLogger(__name__)


class FillerBank:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pcm: dict[str, bytes] = {}
        self.sample_rate = settings.tts_sample_rate
        self.prepared = False

    async def prepare(self, tts: Optional[TTSProvider]) -> None:
        if not self.settings.filler_enabled or tts is None:
            return
        texts = {"zh": self.settings.filler_zh, "en": self.settings.filler_en}
        for lang, text in texts.items():
            try:
                data = await tts.synthesize(text, lang)
                if data:
                    self.pcm[lang] = data
                    self.sample_rate = getattr(tts, "sample_rate", self.sample_rate) or self.sample_rate
            except Exception as exc:  # noqa: BLE001
                log.warning("filler synth failed lang=%s: %s", lang, exc)
        self.prepared = bool(self.pcm)
        log.info("filler ready: %s (%.2fs zh / %.2fs en)", list(self.pcm), *[
            len(self.pcm.get(k, b"")) / 2 / max(1, self.sample_rate)
            for k in ("zh", "en")
        ])

    def get(self, lang: str) -> tuple[bytes, int] | None:
        data = self.pcm.get(lang) or self.pcm.get("zh") or self.pcm.get("en")
        if not data:
            return None
        return data, self.sample_rate

    def status(self) -> dict:
        return {
            "prepared": self.prepared,
            "langs": list(self.pcm),
            "sample_rate": self.sample_rate,
            "deadline_ms": self.settings.filler_deadline_ms,
        }
