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
    cfg.fast_answer = False  # 测试走 LLM 流式路径，不受 .env FAST_ANSWER 影响
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


def test_explicit_page_replaces_open_page_in_answer_context(tmp_path):
    class ContextLLM(LLMProvider):
        async def stream(self, messages, context=None):
            assert "城堡倒了" in context["current_page_text"]
            assert "城堡倒了" in messages[0]["content"]
            assert "一起搭积木城堡" not in messages[0]["content"]
            yield "城堡倒了，花花很生气。"

    cfg, runner, store = make_runner(tmp_path, MockTTS(), ContextLLM())
    cfg.filler_enabled = False

    async def collect():
        return [
            event async for event in runner.run_text(
                "probe", make_book(), "第二页讲了什么？", current_page=1,
            )
        ]

    try:
        answer = next(event for event in asyncio.run(collect()) if event["type"] == "answer")
        assert answer["text"] == "城堡倒了，花花很生气。"
        assert {item["page"] for item in answer["evidence"]} == {2}
    finally:
        store.close()


def test_voice_uses_final_transcript_and_streams_preview(tmp_path):
    class StreamingASR(MockASR):
        async def transcribe_stream(self, audio, sample_rate=16000, language=None, initial_prompt=None):
            yield ASRResult("继续", "zh", partial=True)
            yield ASRResult("暂停", "zh")

    cfg, runner, store = make_runner(tmp_path, MockTTS(), SlowLLM(delay=0), asr=StreamingASR())
    cfg.filler_enabled = False

    async def collect():
        return [event async for event in runner.run_voice("probe", make_book(), bytes(6400))]

    try:
        events = asyncio.run(collect())
        assert [(event["type"], event["text"]) for event in events[:2]] == [
            ("asr_partial", "继续"), ("asr_final", "暂停"),
        ]
        answer = next(event for event in events if event["type"] == "answer")
        assert answer["action"]["type"] == "pause"
        assert answer["metrics"]["first_reply_audio_after_speech_ms"] is not None
    finally:
        store.close()


def test_tts_starts_before_llm_finishes(tmp_path):
    async def run():
        tts_started = asyncio.Event()

        class StreamingLLM(LLMProvider):
            async def stream(self, messages, context=None):
                yield "他们在搭积木，"
                await asyncio.wait_for(tts_started.wait(), timeout=1)
                yield "想搭一座高高的城堡。"

        class StreamingTTS(TTSProvider):
            async def stream(self, text, language=None, *, seq=0):
                if text == "他们在搭积木，":
                    tts_started.set()
                yield bytes(960)

        cfg = Settings(data_dir=tmp_path, filler_enabled=False)
        cfg.fast_answer = False
        cfg.ensure_dirs()
        store = Store(cfg)
        runner = VoiceTurnRunner(cfg, MockASR(), StreamingTTS(), StreamingLLM(), FillerBank(cfg), store)
        try:
            events = [event async for event in runner.run_text("probe", make_book(), "他们在搭什么？")]
            audio_start = next(i for i, event in enumerate(events) if event["type"] == "audio_start")
            second_delta = next(i for i, event in enumerate(events) if event.get("text") == "想搭一座高高的城堡。")
            assert audio_start < second_delta
        finally:
            store.close()

    asyncio.run(run())
