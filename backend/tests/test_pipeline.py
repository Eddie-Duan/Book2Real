from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.book.index import BookIndex
from app.book.store import Store
from app.config import Settings
from app.pipeline.filler import FillerBank
from app.pipeline.turn import VoiceTurnRunner
from app.providers.base import ASRProvider, ASRResult, LLMProvider, TTSProvider
from app.utils import now_ms


class MockASR(ASRProvider):
    async def transcribe(self, audio, sample_rate=16000, language=None, partial=False, initial_prompt=None):
        return ASRResult("他们在搭什么？", "zh", 500.0, partial)


class MockTTS(TTSProvider):
    name = "mock-tts"
    sample_rate = 24000

    def __init__(self, delay: float = 0.01, fail: bool = False):
        self.delay = delay
        self.fail = fail
        self.requests = 0

    async def stream(self, text, language=None, *, seq=0):
        self.requests += 1
        if self.fail:
            raise RuntimeError("tts down")
        await asyncio.sleep(self.delay)
        yield b"\x00\x00" * 1200


class SlowLLM(LLMProvider):
    def __init__(self, delay: float = 0.8, text: str = "他们在搭积木城堡。"):
        self.delay = delay
        self.text = text

    async def stream(self, messages, context=None):
        await asyncio.sleep(self.delay)
        yield self.text


def make_book() -> BookIndex:
    return BookIndex(
        {
            "id": "demo",
            "title": "演示绘本",
            "lang": "zh",
            "page_count": 2,
            "pages": [
                {"no": 1, "story_text": "小满和花花一起搭积木城堡。", "teaching_text": "", "blocks": []},
                {"no": 2, "story_text": "积木城堡倒了，花花很生气。", "teaching_text": "", "blocks": []},
            ],
            "elements": [],
            "script": {"items": []},
        }
    )


def make_runner(tmp_path: Path, tts: TTSProvider, llm: LLMProvider, asr=None, filler_deadline=300):
    cfg = Settings()
    cfg.data_dir = tmp_path
    cfg.filler_enabled = True
    cfg.filler_deadline_ms = filler_deadline
    cfg.tts_sample_rate = 24000
    cfg.moderation_enabled = False
    cfg.session_history_turns = 4
    cfg.ensure_dirs()
    store = Store(cfg)
    filler = FillerBank(cfg)
    asyncio.run(filler.prepare(tts))
    runner = VoiceTurnRunner(cfg, asr or MockASR(), tts, llm, filler, store)
    return cfg, runner, store


def test_filler_guarantees_early_audio(tmp_path):
    tts = MockTTS(delay=0.01)
    cfg, runner, store = make_runner(tmp_path, tts, SlowLLM(delay=0.8))

    async def collect():
        events = []
        speech_end = now_ms()
        async for event in runner.run_text("probe", make_book(), "他们在搭什么？", speech_end_ms=speech_end):
            events.append((now_ms() - speech_end, event))
        return events

    events = asyncio.run(collect())
    kinds = [e["type"] for _, e in events]
    assert "filler_used" in kinds
    answer = [e for _, e in events if e["type"] == "answer"][-1]
    assert answer["metrics"]["used_filler"] is True
    assert answer["metrics"]["first_audio_after_speech_ms"] <= 1000


def test_direct_playback_fast(tmp_path):
    tts = MockTTS(delay=0.01)
    cfg, runner, store = make_runner(tmp_path, tts, SlowLLM(delay=5))

    async def collect():
        events = []
        speech_end = now_ms()
        async for event in runner.run_text("probe", make_book(), "暂停", speech_end_ms=speech_end):
            events.append((now_ms() - speech_end, event))
        return events

    events = asyncio.run(collect())
    answer = [e for _, e in events if e["type"] == "answer"][-1]
    assert answer["text"] == "好的，暂停。"
    assert answer["metrics"]["first_audio_after_speech_ms"] < 500
    assert answer["metrics"]["used_filler"] is False


def test_tts_failure_still_returns_text(tmp_path):
    tts = MockTTS(fail=True)
    cfg, runner, store = make_runner(tmp_path, tts, SlowLLM(delay=0.01))
    cfg.filler_enabled = False

    async def collect():
        events = []
        async for event in runner.run_text("probe", make_book(), "他们在搭什么？", speech_end_ms=now_ms()):
            events.append(event)
        return events

    events = asyncio.run(collect())
    answer = [e for e in events if e["type"] == "answer"][-1]
    assert answer["text"]
    assert any(e["type"] == "tts_error" for e in events)
