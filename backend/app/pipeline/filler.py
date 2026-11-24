"""垫话库：预合成 5 句，用掉一句后台补一句；首音 0ms 起播。"""
from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from typing import Optional

from ..config import Settings
from ..providers.base import TTSProvider
from . import acks

log = logging.getLogger(__name__)


class FillerBank:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.items: dict[str, list[tuple[str, bytes]]] = {"zh": [], "en": []}
        self.pcm: dict[str, bytes] = {}
        self.sample_rate = settings.tts_sample_rate
        self.prepared = False
        self.target = max(1, int(getattr(settings, "ack_prewarm_lines", 5) or 5))
        self._tts: Optional[TTSProvider] = None
        self._queue: dict[str, deque[str]] = {"zh": deque(), "en": deque()}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._recent: list[str] = []
        self._seed_banks()

    def _seed_banks(self) -> None:
        banks = {"zh": list(acks.ZH_ACKS), "en": list(acks.EN_ACKS)}
        banks["zh"].append(self.settings.filler_zh)
        banks["en"].append(self.settings.filler_en)
        for lang, lines in banks.items():
            seen: set[str] = set()
            unique = []
            for text in lines:
                text = (text or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    unique.append(text)
            random.shuffle(unique)
            self._queue[lang] = deque(unique)

    async def _synth(self, lang: str, text: str) -> bool:
        if self._tts is None:
            return False
        try:
            data = await self._tts.synthesize(text, lang)
            if not data:
                return False
            self.items[lang].append((text, data))
            if lang not in self.pcm:
                self.pcm[lang] = data
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("ack synth failed lang=%s text=%r: %s", lang, text, exc)
            return False

    async def _fill_initial(self, lang: str, sem: asyncio.Semaphore) -> None:
        while len(self.items[lang]) < self.target and self._queue[lang]:
            text = self._queue[lang].popleft()
            async with sem:
                if not await self._synth(lang, text):
                    self._queue[lang].append(text)
                    break

    async def prepare(self, tts: Optional[TTSProvider]) -> None:
        if not self.settings.filler_enabled or tts is None or self.prepared:
            return
        self._tts = tts
        sem = asyncio.Semaphore(2)
        await asyncio.gather(*(self._fill_initial(lang, sem) for lang in ("zh", "en")))
        self.sample_rate = getattr(tts, "sample_rate", self.sample_rate) or self.sample_rate
        self.prepared = bool(self.items.get("zh") or self.items.get("en"))
        log.info(
            "ack bank ready: zh=%d en=%d target=%d rate=%s",
            len(self.items.get("zh", [])),
            len(self.items.get("en", [])),
            self.target,
            self.sample_rate,
        )

    def _schedule_refill(self, lang: str) -> None:
        if not getattr(self.settings, "ack_refill", True):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._refill(lang))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _refill(self, lang: str) -> None:
        async with self._lock:
            if len(self.items[lang]) >= self.target or not self._queue[lang]:
                return
            text = self._queue[lang].popleft()
            if not await self._synth(lang, text):
                self._queue[lang].append(text)

    def get(self, lang: str) -> tuple[str, bytes, int] | None:
        key = "en" if (lang or "").lower().startswith("en") else "zh"
        bank = self.items.get(key) or self.items.get("zh") or self.items.get("en") or []
        if not bank:
            return None
        available = [i for i, item in enumerate(bank) if item[0] not in self._recent[-3:]]
        idx = random.choice(available or list(range(len(bank))))
        text, data = bank.pop(idx)
        self._recent.append(text)
        self._recent = self._recent[-12:]
        # 用掉一句，马上把这句话放回队列尾部，并后台补一句新的
        self._queue[key].append(text)
        self._schedule_refill(key)
        return text, data, self.sample_rate

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def status(self) -> dict:
        return {
            "prepared": self.prepared,
            "langs": [k for k, v in self.items.items() if v],
            "lines": {k: len(v) for k, v in self.items.items()},
            "prewarm": self.target,
            "queued": {k: len(v) for k, v in self._queue.items()},
            "sample_rate": self.sample_rate,
            "deadline_ms": self.settings.filler_deadline_ms,
        }
