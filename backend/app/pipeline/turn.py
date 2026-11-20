"""流式语音回合：ASR → 意图/检索 → LLM 流 → 小句切分 → TTS 流 → 首音。

关键延迟设计：
- LLM 第一个小句一到就启动 TTS，不等整段回答生成完；
- speech_end + FILLER_DEADLINE_MS 仍没有真音频时，先播预合成的自然垫音；
- 所有时间戳从 speech_end 起算，写进 SQLite 供 p50/p95 统计。
"""
from __future__ import annotations

import asyncio
import logging
import numpy as np
import re
import time
from typing import AsyncIterator, Optional

from ..config import Settings
from ..providers.base import ASRProvider, LLMProvider, TTSProvider
from ..utils import detect_lang, now_ms, pcm_duration_ms, truncate
from ..book.index import BookIndex, route_intent
from ..book.store import Store
from .filler import FillerBank
from .latency import TurnTimeline
from .segmenter import ClauseSplitter
from .vad import trim_speech

log = logging.getLogger(__name__)

_SAFE_REPLY = "我们换个话题吧，继续看绘本好不好？"
_BLOCK_WORDS = ("杀", "自杀", "色情", "裸体", "毒品", "blood", "sex", "kill yourself")


def _moderate(text: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    low = (text or "").lower()
    if any(w in low for w in _BLOCK_WORDS):
        return _SAFE_REPLY
    return text


def build_messages(
    query: str,
    book: dict,
    intent: dict,
    history: list[dict],
    current_page: Optional[int],
) -> list[dict]:
    passages = intent.get("passages") or []
    ctx_lines = []
    if current_page:
        page = next((p for p in book.get("pages", []) if int(p.get("no") or 0) == int(current_page)), None)
        page_text = (page or {}).get("story_text", "")
        if page_text:
            ctx_lines.append(f"[当前页 {current_page}] {truncate(page_text.replace(chr(10), ' '), 320)}")
    for item in passages[:6]:
        page = item.get("page")
        text = truncate(str(item.get("text") or "").replace("\n", " "), 280)
        if text:
            ctx_lines.append(f"[第{page}页] {text}")
    context = "\n".join(ctx_lines) if ctx_lines else "（没有检索到直接相关的段落）"
    lang = intent.get("lang") or detect_lang(query)
    system = (
        "你是一位温柔、有耐心的儿童绘本伴读老师，面向 5-8 岁孩子。\n"
        f"当前绘本：《{book.get('title', '')}》，书页语言：{book.get('lang', 'zh')}。\n"
        f"当前页码：{current_page or '未知'}。\n"
        "回答要以下面提供的绘本原文为依据；原文没明说的细节，可以结合故事情节合理推断，但不要编造与故事无关的内容。\n"
        "规则：\n"
        "1. 用孩子提问的语言回答（中文问中文答，英文问英文答）。\n"
        "2. 只答 1-2 句，中文不超过 45 个字，英文不超过 30 个单词。\n"
        "3. 直接回答，不要重复问题，不要开场白，不要 markdown、列表、emoji。\n"
        "4. 为什么类问题优先用故事里的因果；若原文没明说，可根据画面动作合理推断（如积木越搭越高所以倒了），不要只说“绘本没讲”。词语解释要用孩子能懂的话。\n"
        f"绘本原文：\n{context}"
    )
    if intent.get("intent") == "chitchat":
        system = (
            "你是儿童绘本伴读助手“纸书智伴”。孩子现在问的是与绘本内容无关的话（打招呼、测试麦克风/声音、闲聊）。"
            "用孩子提问的语言，亲切自然地直接回答，1-2 句，不超过 40 个字；不要提绘本原文，不要 markdown、列表、emoji。"
            "如果孩子在测试声音/麦克风，就确认你听得到并简单回应。"
        )
    messages = [{"role": "system", "content": system}]
    for msg in history[-8:]:
        if msg.get("content"):
            messages.append({"role": msg.get("role", "user"), "content": msg["content"]})
    messages.append({"role": "user", "content": query})
    if lang == "en" and book.get("lang") == "zh":
        messages[-1]["content"] = query + "\n(Please answer in English based on the Chinese book context above.)"
    return messages


class VoiceTurnRunner:
    def __init__(
        self,
        settings: Settings,
        asr: ASRProvider,
        tts: TTSProvider,
        llm: LLMProvider,
        filler: FillerBank,
        store: Store,
    ) -> None:
        self.settings = settings
        self.asr = asr
        self.tts = tts
        self.llm = llm
        self.filler = filler
        self.store = store

    async def run_voice(
        self,
        session_id: str,
        book: BookIndex,
        audio: bytes,
        sample_rate: int = 16000,
        *,
        language: Optional[str] = None,
        current_page: Optional[int] = None,
        current_element: Optional[dict] = None,
        speech_end_ms: Optional[float] = None,
        warmup: bool = False,
        video_index=None,
    ) -> AsyncIterator[dict]:
        speech_end = speech_end_ms or now_ms()
        pcm, trim_start, trim_end = trim_speech(audio, sample_rate)
        # 低音量麦克风兜底：峰值过低时归一化放大，避免 ASR 收到近静音
        if pcm:
            peak = max(abs(int.from_bytes(pcm[i:i+2], "little", signed=True)) for i in range(0, min(len(pcm), 200000), 2))
            if 0 < peak < 6000:
                scale = min(6.0, 16000.0 / peak)
                arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32) * scale
                pcm = np.clip(arr, -32768, 32767).astype("<i2").tobytes()
        t_asr = time.perf_counter()
        try:
            result = await self.asr.transcribe(pcm, sample_rate, language=language, partial=False, initial_prompt=book.asr_initial_prompt())
            asr_ms = (time.perf_counter() - t_asr) * 1000
        except Exception as exc:  # noqa: BLE001
            log.exception("ASR failed")
            yield {"type": "error", "stage": "asr", "message": str(exc)}
            return
        if not result.text.strip():
            dur_ms = pcm_duration_ms(pcm, sample_rate)
            hint = (
                "只录到很短的声音，可能被浏览器降噪截断了。请改用“按住说话”、关闭降噪，或提高增益/靠近麦克风。"
                if dur_ms < 500
                else "我没有听清，可以再说一遍吗？"
            )
            yield {
                "type": "asr_final",
                "text": "",
                "lang": language or "zh",
                "ms": round(asr_ms, 1),
                "trim": [round(trim_start, 1), round(trim_end, 1)],
            }
            yield {"type": "answer", "text": hint, "lang": language or "zh"}
            return
        yield {
            "type": "asr_final",
            "text": result.text,
            "lang": result.lang,
            "ms": round(asr_ms, 1),
            "trim": [round(trim_start, 1), round(trim_end, 1)],
        }
        async for event in self.run_text(
            session_id,
            book,
            result.text,
            language=result.lang,
            current_page=current_page,
            current_element=current_element,
            speech_end_ms=speech_end,
            asr_ms=asr_ms,
            warmup=warmup,
            video_index=video_index,
        ):
            yield event

    async def run_text(
        self,
        session_id: str,
        book: BookIndex,
        query: str,
        *,
        language: Optional[str] = None,
        current_page: Optional[int] = None,
        current_element: Optional[dict] = None,
        speech_end_ms: Optional[float] = None,
        asr_ms: float = 0.0,
        warmup: bool = False,
        video_index=None,
    ) -> AsyncIterator[dict]:
        speech_end = speech_end_ms or now_ms()
        timeline = TurnTimeline(speech_end_ms=speech_end)
        timeline.mark("asr_final", now_ms())
        intent = route_intent(query, book, current_page=current_page, current_element=current_element)
        lang = language or intent.get("lang") or book.book.get("lang", "zh")
        intent["lang"] = lang
        self._attach_video_action(intent, video_index)
        history = self.store.recent_context(session_id, self.settings.session_history_turns)
        self.store.add_message(
            session_id,
            "user",
            query,
            lang,
            current_page,
            element=(current_element or {}).get("name", ""),
            image=(current_element or {}).get("image", ""),
        )

        queue: asyncio.Queue = asyncio.Queue()
        clause_queue: asyncio.Queue = asyncio.Queue()
        state: dict = {
            "first_audio": False,
            "used_filler": False,
            "answer": "",
            "answer_done": False,
            "tts_done": False,
            "audio_seq": 0,
            "cancelled": False,
            "tts_error": "",
            "llm_first_token": None,
            "tts_first_chunk": None,
            "answer_first_clause": None,
            "filler_seq": None,
        }
        splitter = ClauseSplitter(max_chars=self.settings.tts_max_clause_chars)

        async def emit(event: dict) -> None:
            await queue.put(event)

        async def answer_task() -> None:
            try:
                timeline.mark("llm_start", now_ms())
                if intent.get("answer"):
                    text = intent["answer"]
                    await emit({"type": "answer_delta", "text": text})
                    for clause in splitter.feed(text):
                        if state["answer_first_clause"] is None:
                            state["answer_first_clause"] = now_ms()
                            timeline.mark("answer_first_clause", state["answer_first_clause"])
                        await clause_queue.put(clause)
                    state["answer"] = text
                else:
                    messages = build_messages(query, book.book, intent, history, current_page)
                    page_obj = book.page(current_page) if current_page else None
                    context = {
                        "query": query,
                        "passages": intent.get("passages", []),
                        "lang": lang,
                        "intent": intent.get("intent"),
                        "current_page_text": (page_obj or {}).get("story_text", ""),
                    }
                    produced = ""
                    try:
                        async for delta in self.llm.stream(messages, context=context):
                            if state["cancelled"]:
                                break
                            if not delta:
                                continue
                            if state["llm_first_token"] is None:
                                state["llm_first_token"] = now_ms()
                                timeline.mark("llm_first_token", state["llm_first_token"])
                            if not produced:
                                delta = delta.lstrip()
                                if not delta:
                                    continue
                            produced += delta
                            await emit({"type": "answer_delta", "text": delta})
                            for clause in splitter.feed(delta):
                                if state["answer_first_clause"] is None:
                                    state["answer_first_clause"] = now_ms()
                                    timeline.mark("answer_first_clause", state["answer_first_clause"])
                                await clause_queue.put(clause)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("LLM failed, using extractive fallback")
                        produced = ""
                    if not produced.strip():
                        from ..providers.llm import compose_extractive

                        page_obj = book.page(current_page) if current_page else None
                        produced = compose_extractive(
                            query,
                            intent.get("passages", []),
                            lang,
                            current_page_text=(page_obj or {}).get("story_text", ""),
                        )
                        await emit({"type": "answer_delta", "text": produced})
                        for clause in splitter.feed(produced):
                            await clause_queue.put(clause)
                    state["answer"] = produced
                for clause in splitter.flush():
                    await clause_queue.put(clause)
                await clause_queue.put(None)
            except Exception as exc:  # noqa: BLE001
                log.exception("answer task failed")
                await emit({"type": "error", "stage": "answer", "message": str(exc)})
                await clause_queue.put(None)
            finally:
                state["answer_done"] = True
                await emit(
                    {
                        "type": "_answer_done",
                        "text": state["answer"],
                        "intent": intent.get("intent"),
                        "action": intent.get("action"),
                        "evidence": intent.get("evidence", []),
                    }
                )

        async def tts_task() -> None:
            try:
                while True:
                    clause = await clause_queue.get()
                    if clause is None:
                        break
                    if state["cancelled"]:
                        continue
                    seq = state["audio_seq"]
                    state["audio_seq"] += 1
                    started = False
                    try:
                        async for chunk in self.tts.stream(clause, lang, seq=seq):
                            if state["cancelled"] or not chunk:
                                continue
                            if not started:
                                started = True
                                if state["tts_first_chunk"] is None:
                                    state["tts_first_chunk"] = now_ms()
                                    timeline.mark("tts_start", state["tts_first_chunk"])
                                await emit(
                                    {
                                        "type": "audio_start",
                                        "seq": seq,
                                        "sample_rate": getattr(self.tts, "sample_rate", self.settings.tts_sample_rate),
                                        "format": "pcm16",
                                        "text": clause,
                                    }
                                )
                            if not state["first_audio"]:
                                state["first_audio"] = True
                                timeline.mark("first_audio", now_ms())
                            await emit({"type": "audio", "seq": seq, "pcm": chunk})
                        if started:
                            await emit({"type": "audio_end", "seq": seq})
                    except Exception as exc:  # noqa: BLE001
                        state["tts_error"] = str(exc)
                        log.warning("TTS clause failed: %s", exc)
                        await emit({"type": "tts_error", "seq": seq, "message": str(exc)})
                        break
            finally:
                state["tts_done"] = True
                await emit({"type": "_tts_done"})

        async def filler_task() -> None:
            # deadline 从“用户真正说完”起算；turn 启动时已经过了 VAD 尾音时间
            delay = max(0.0, (speech_end + self.settings.filler_deadline_ms - now_ms()) / 1000.0)
            await asyncio.sleep(delay)
            if state["first_audio"] or state["cancelled"]:
                return
            item = self.filler.get(lang)
            if not item:
                return
            pcm, rate = item
            state["used_filler"] = True
            state["first_audio"] = True
            seq = 900000 + int(time.time() * 1000) % 1000
            state["filler_seq"] = seq
            timeline.mark("filler_audio", now_ms())
            timeline.mark("first_audio", now_ms())
            await emit({"type": "audio_start", "seq": seq, "sample_rate": rate, "format": "pcm16", "text": "filler", "filler": True})
            step = max(2, int(rate * 0.1) * 2)
            for i in range(0, len(pcm), step):
                piece = pcm[i : i + step]
                if piece:
                    await emit({"type": "audio", "seq": seq, "pcm": piece})
            await emit({"type": "audio_end", "seq": seq, "filler": True})
            await emit({"type": "filler_used", "text": self.settings.filler_zh if lang == "zh" else self.settings.filler_en})

        tasks = [asyncio.create_task(tts_task()), asyncio.create_task(answer_task())]
        if self.settings.filler_enabled:
            tasks.append(asyncio.create_task(filler_task()))
        try:
            while True:
                event = await queue.get()
                if event["type"] in ("_answer_done", "_tts_done"):
                    if event["type"] == "_answer_done":
                        state["answer_done"] = True
                        state["action"] = event.get("action")
                        state["intent"] = event.get("intent")
                        state["evidence"] = event.get("evidence", [])
                    else:
                        state["tts_done"] = True
                    if state["answer_done"] and state["tts_done"]:
                        break
                    continue
                yield event
        finally:
            state["cancelled"] = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        answer = _moderate(state["answer"] or "", self.settings.moderation_enabled)
        if answer != state.get("answer") and answer:
            # 安全兜底：若原文触发内容过滤，改用安全短句（已播出的音频无法撤回，至少文本安全）
            state["answer"] = answer
        timeline.mark("done", now_ms())
        done_ms = now_ms()
        metrics = timeline.summary()
        metrics.update(
            {
                "used_filler": state["used_filler"],
                "tts_error": state["tts_error"],
                "asr_ms": round(asr_ms, 1),
                "first_audio_after_speech_ms": round(timeline.since_speech_end("first_audio"), 1) if state["first_audio"] else None,
            }
        )
        self.store.record_turn(
            session_id,
            book.book.get("id", ""),
            speech_end,
            timeline.marks.get("first_audio"),
            done_ms,
            asr_ms,
            (state["llm_first_token"] - timeline.marks.get("llm_start", speech_end)) if state["llm_first_token"] else 0.0,
            (state["tts_first_chunk"] - timeline.marks.get("llm_start", speech_end)) if state["tts_first_chunk"] else 0.0,
            state["used_filler"],
            warmup,
            query,
            state["answer"],
            metrics,
        )
        self.store.add_message(
            session_id,
            "assistant",
            state["answer"],
            lang,
            current_page,
            element=(current_element or {}).get("name", ""),
            action=state.get("action"),
        )
        yield {
            "type": "answer",
            "text": state["answer"],
            "lang": lang,
            "intent": state.get("intent"),
            "action": state.get("action"),
            "evidence": state.get("evidence", []),
            "timings": metrics,
            "metrics": {
                "first_audio_after_speech_ms": metrics.get("first_audio_after_speech_ms"),
                "asr_ms": metrics.get("asr_ms"),
                "used_filler": state["used_filler"],
                "budget_ms": self.settings.latency_budget_ms,
                "hit": bool(metrics.get("first_audio_after_speech_ms") is not None and metrics["first_audio_after_speech_ms"] <= self.settings.latency_budget_ms),
            },
        }
        yield {"type": "done", "timings": metrics}

    @staticmethod
    def _attach_video_action(intent: dict, video_index) -> None:
        if not video_index:
            return
        action = intent.get("action") or {}
        if intent.get("intent") == "find_text":
            needle = action.get("text")
            if needle:
                hits = video_index.search_text(needle)
                if hits:
                    action.update({"frame_t": hits[0]["t"], "bbox": hits[0].get("bbox"), "video_hits": hits[:8]})
                    intent["action"] = action
        elif intent.get("intent") == "find_scene" and action.get("page"):
            scene = video_index.scene_for_page(int(action["page"]))
            if scene:
                action.update({"t": scene.get("start"), "end": scene.get("end")})
                intent["action"] = action
