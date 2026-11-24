from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator, Optional

from app.book.index import BookIndex
from app.book.store import Store
from app.config import Settings
from app.pipeline.filler import FillerBank
from app.pipeline.turn import VoiceTurnRunner
from app.providers.base import LLMProvider, TTSProvider
from app.utils import now_ms


class RecordingTTS(TTSProvider):
    name = "recording-tts"
    sample_rate = 24000

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def stream(self, text: str, language: Optional[str] = None, *, seq: int = 0) -> AsyncIterator[bytes]:
        self.texts.append(text)
        yield b"\x00\x00" * 1200


class CorrectingLLM(LLMProvider):
    async def stream(self, messages: list[dict], context: Optional[dict] = None) -> AsyncIterator[str]:
        yield "原文没写小满为什么难过，只写花花生气并怪小满。"


def make_book() -> BookIndex:
    return BookIndex(
        {
            "id": "demo",
            "title": "一起搭积木吧",
            "lang": "zh",
            "page_count": 12,
            "pages": [
                {"no": 6, "story_text": "高高积木倒一地，花花很生气。“小满，都怪你！”“我不要和小满玩！”“我也不和花花玩！”", "teaching_text": "", "blocks": []},
                {"no": 8, "story_text": "花花的生日到了，可是花花一点儿也不开心。“想和小满一起过生日。”", "teaching_text": "", "blocks": []},
                {"no": 11, "story_text": "“花花，对不起！”“我早就不生气了。”花花好开心。", "teaching_text": "", "blocks": []},
            ],
            "elements": [],
            "script": {"items": []},
        }
    )


def test_wrong_extractive_is_not_spoken(tmp_path: Path):
    cfg = Settings(data_dir=tmp_path)
    cfg.data_dir = tmp_path
    cfg.fast_answer = True
    cfg.verified_answers = True
    cfg.filler_enabled = True
    cfg.filler_deadline_ms = 300
    cfg.moderation_enabled = False
    cfg.ensure_dirs()
    store = Store(cfg)
    tts = RecordingTTS()

    async def run():
        filler = FillerBank(cfg)
        await filler.prepare(tts)
        runner = VoiceTurnRunner(cfg, None, tts, CorrectingLLM(), filler, store)  # type: ignore[arg-type]
        events = [e async for e in runner.run_text("probe", make_book(), "小满为什么难过？", speech_end_ms=now_ms())]
        return events

    try:
        events = asyncio.run(run())
    finally:
        store.close()

    answer = [e for e in events if e["type"] == "answer"][-1]
    assert answer["text"].startswith("不是小满，是花花")
    assert answer["metrics"]["verified"] is True
    assert answer["metrics"]["first_audio_after_speech_ms"] <= 1000

    spoken = [e.get("text", "") for e in events if e["type"] == "audio_start" and not e.get("filler")]
    assert any("不是小满" in t for t in spoken)
    assert not any("花花的生日到了" in t for t in spoken)
