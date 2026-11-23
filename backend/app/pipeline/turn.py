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
from contextlib import aclosing
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
from ..skill4 import build_style_card, style_fallback_text, style_prompt, style_touch_text

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
    style_card: Optional[dict] = None,
) -> list[dict]:
    passages = intent.get("passages") or []
    mode = "touch" if intent.get("intent") == "touch" else "qa"
    book_lang = str(book.get("lang") or "zh")
    lang = intent.get("lang") or detect_lang(query)
    answer_lang = book_lang if mode == "touch" else lang
    if style_card is not None:
        card = style_card
    elif current_page:
        # 指定了页码时，文风卡只用这一页的原文，避免把别的页的句子带进提示词
        only = next(
            (pg for pg in (book.get("pages") or [])
             if int(pg.get("no") or 0) == int(current_page)),
            None,
        )
        card = build_style_card(
            {"title": book.get("title"), "lang": book.get("lang"),
             "pages": [only] if only else []}
        )
    else:
        card = build_style_card(book)
    style_block = style_prompt(card, answer_lang=answer_lang, mode=mode)

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
    element = next(
        (e.get("element") for e in (intent.get("evidence") or [])
         if isinstance(e, dict) and isinstance(e.get("element"), dict)),
        None,
    )
    if element:
        name = str(element.get("name") or "").strip()
        info = str(element.get("info") or "").strip()
        if name or info:
            ctx_lines.append(f"[点击元素] {name}：{truncate(info, 220)}")
    context = "\n".join(ctx_lines) if ctx_lines else "（没有检索到直接相关的段落）"

    language_rule = (
        f"使用绘本语言（{book_lang}）回答，即使孩子用另一种语言提问。"
        if mode == "touch"
        else "用孩子提问的语言回答（中文问中文答，英文问英文答）。"
    )
    mode_rule = (
        "这是孩子点击画面物体后的回答：先用一句话说它是什么、在做什么，再补一个本页画面细节；不要百科式介绍，不要列事实清单。"
        if mode == "touch"
        else "为什么类问题优先用故事里的因果；若原文没明说，可根据画面动作做一步合理推断，不要只说“绘本没讲”。词语解释要用孩子能懂的话。"
    )
    length_rule = (
        "只答 1 句，中文不超过 30 个字，英文不超过 20 个单词。"
        if mode == "touch"
        else "只答 1-2 句，中文不超过 45 个字，英文不超过 30 个单词。"
    )
    system = (
        "你是一位温柔、有耐心的儿童绘本伴读老师，正在用这本书自己的声音和孩子说话。\n"
        f"当前绘本：《{book.get('title', '')}》，书页语言：{book_lang}。\n"
        f"当前页码：{current_page or '未知'}。\n"
        "回答要以下面的绘本原文和本页画面信息为依据；不要新增原文没有的人物、地点、数字或与故事无关的内容。\n"
        f"{style_block}\n"
        "规则：\n"
        "0. 严禁输出思考过程、分析或 <think> 标签，第一个字就是最终回答。\n"
        f"1. {language_rule}\n"
        f"2. {length_rule}\n"
        "3. 直接回答，不要重复问题，不要开场白，不要 markdown、列表、emoji。\n"
        f"4. {mode_rule}\n"
        "5. 不要使用“根据资料／作为一个 AI／绘本里说／书上说／总之／希望对你有帮助”这类套话。\n"
        f"绘本原文：\n{context}"
    )
    if intent.get("target_page") is not None:
        system += f"\n问题指定了第 {intent['target_page']} 页。只使用这页提供的文字和标注，缺少依据的细节请如实说明。"
        history = []
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
        chitchat_cache=None,
    ) -> None:
        self.settings = settings
        self.asr = asr
        self.tts = tts
        self.llm = llm
        self.filler = filler
        self.store = store
        self.chitchat_cache = chitchat_cache

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
        first_asr_text_ms = None
        result = None
        prewarm = None
        prewarm_query = None
        prewarm_count = 0
        try:
            events = self.asr.transcribe_stream(
                pcm, sample_rate, language=language, initial_prompt=book.asr_initial_prompt(),
            )
            async with aclosing(events):
                async for update in events:
                    if not update.partial:
                        result = update
                        break
                    if update.text:
                        elapsed = round((time.perf_counter() - t_asr) * 1000, 1)
                        if first_asr_text_ms is None:
                            first_asr_text_ms = elapsed
                        yield {"type": "asr_partial", "text": update.text, "lang": update.lang, "ms": elapsed}
                        # 投机预生成：ASR 部分文本已足够长时，提前发起 LLM 请求，
                        # 与 ASR 收尾重叠，省掉 200-500ms 首 token 等待。
                        text = update.text.strip()
                        if (
                            prewarm_count < 2
                            and len(text) >= 5
                            and text != prewarm_query
                            and hasattr(self.llm, "prewarm")
                        ):
                            intent_p = route_intent(text, book, current_page=current_page, current_element=current_element)
                            if intent_p.get("intent") in ("qa", "definition", "chitchat"):
                                if prewarm is not None and prewarm.task is not None:
                                    prewarm.task.cancel()
                                lang_p = update.lang or intent_p.get("lang") or "zh"
                                intent_p["lang"] = lang_p
                                history_p = self.store.recent_context(session_id, self.settings.session_history_turns)
                                msgs_p = build_messages(text, book.book, intent_p, history_p, intent_p.get("target_page", current_page))
                                prewarm = self.llm.prewarm(msgs_p)
                                prewarm_query = text
                                prewarm_count += 1
            if result is None:
                raise RuntimeError("ASR returned no final transcription")
            asr_ms = (time.perf_counter() - t_asr) * 1000
        except Exception as exc:  # noqa: BLE001
            log.exception("ASR failed")
            yield {"type": "error", "stage": "asr", "message": str(exc)}
            return
        if not result.text.strip():
            dur_ms = pcm_duration_ms(pcm, sample_rate)
            hint = (
                "刚才只录到很短的声音。请按住说话，说完再松开。"
                if dur_ms < 500
                else "我没有听清，可以再说一遍吗？"
            )
            yield {
                "type": "asr_final",
                "text": "",
                "lang": language or "zh",
                "ms": round(asr_ms, 1),
                "first_text_ms": first_asr_text_ms,
                "trim": [round(trim_start, 1), round(trim_end, 1)],
            }
            yield {"type": "answer", "text": hint, "lang": language or "zh"}
            return
        yield {
            "type": "asr_final",
            "text": result.text,
            "lang": result.lang,
            "ms": round(asr_ms, 1),
            "first_text_ms": first_asr_text_ms,
            "trim": [round(trim_start, 1), round(trim_end, 1)],
        }
        if prewarm is not None:
            from ..utils import normalize_text as _norm
            if _norm(prewarm_query or "") != _norm(result.text):
                if prewarm.task is not None:
                    prewarm.task.cancel()
                prewarm = None
            else:
                log.info("prewarm reused: %r (tokens=%d)", prewarm_query, len(prewarm.tokens))
        events = self.run_text(
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
            prewarm=prewarm,
        )
        async with aclosing(events):
            async for event in events:
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
        prewarm=None,
    ) -> AsyncIterator[dict]:
        speech_end = speech_end_ms or now_ms()
        timeline = TurnTimeline(speech_end_ms=speech_end)
        timeline.mark("asr_final", now_ms())
        intent = route_intent(query, book, current_page=current_page, current_element=current_element)
        answer_page = intent.get("target_page", current_page)
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
                style_card = build_style_card(book.book)
                use_llm_for_touch = (
                    intent.get("intent") == "touch" and self.settings.style_touch_llm
                )
                cached = None
                if intent.get("intent") == "chitchat" and self.chitchat_cache is not None:
                    cached = self.chitchat_cache.lookup(query)
                if cached:
                    # 预热缓存命中：LLM 真答案 + 预合成音频，直接整段入 TTS 队列旁路
                    state["llm_first_token"] = now_ms()
                    timeline.mark("llm_first_token", state["llm_first_token"])
                    await emit({"type": "answer_delta", "text": cached["text"]})
                    state["answer"] = cached["text"]
                    state["cached_audio"] = cached
                    await clause_queue.put(None)
                elif intent.get("answer") and not use_llm_for_touch:
                    text = intent["answer"]
                    if intent.get("intent") == "touch":
                        text = style_touch_text(text, book.book.get("lang", "zh"))
                    await emit({"type": "answer_delta", "text": text})
                    for clause in splitter.feed(text):
                        if state["answer_first_clause"] is None:
                            state["answer_first_clause"] = now_ms()
                            timeline.mark("answer_first_clause", state["answer_first_clause"])
                        await clause_queue.put(clause)
                    state["answer"] = text
                elif self.settings.fast_answer and intent.get("intent") in ("qa", "definition") and intent.get("passages"):
                    # 快路径：MiniMax LLM 强制 thinking（正文首字 1.4-3s），儿童绘本问答
                    # 直接用检索原文组织回答（0ms），保证语音秒回；chitchat 仍走 LLM。
                    from ..providers.llm import compose_extractive

                    page_obj = book.page(answer_page) if answer_page else None
                    quick = compose_extractive(
                        query, intent.get("passages", []), lang,
                        current_page_text=(page_obj or {}).get("story_text", ""),
                    )
                    state["llm_first_token"] = now_ms()
                    timeline.mark("llm_first_token", state["llm_first_token"])
                    await emit({"type": "answer_delta", "text": quick})
                    for clause in splitter.feed(quick):
                        if state["answer_first_clause"] is None:
                            state["answer_first_clause"] = now_ms()
                            timeline.mark("answer_first_clause", state["answer_first_clause"])
                        await clause_queue.put(clause)
                    state["answer"] = quick
                    state["fast_path"] = True
                else:
                    messages = build_messages(
                        query, book.book, intent, history, answer_page,
                        # 指定了页码时让 build_messages 用「这一页」建文风卡，
                        # 避免整本书的样句（别的页原文）混进提示词
                        style_card=None if answer_page else style_card,
                    )
                    page_obj = book.page(answer_page) if answer_page else None
                    context = {
                        "query": query,
                        "passages": intent.get("passages", []),
                        "lang": lang,
                        "intent": intent.get("intent"),
                        "current_page_text": (page_obj or {}).get("story_text", ""),
                        "style": True,
                    }
                    if prewarm is not None:
                        context["_prewarm"] = prewarm
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

                        page_obj = book.page(answer_page) if answer_page else None
                        fallback = intent.get("answer") or compose_extractive(
                            query,
                            intent.get("passages", []),
                            lang,
                            current_page_text=(page_obj or {}).get("story_text", ""),
                        )
                        if intent.get("intent") == "touch":
                            fallback = style_touch_text(fallback, book.book.get("lang", "zh"))
                        else:
                            fallback = style_fallback_text(
                                fallback, answer_lang=lang, mode="qa",
                            )
                        produced = fallback
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
                        # 缓存命中：直接播预合成音频（0ms 合成）
                        cached = state.get("cached_audio")
                        if cached and not state["cancelled"]:
                            seq = state["audio_seq"]
                            state["audio_seq"] += 1
                            rate = cached["rate"]
                            self.tts.sample_rate = rate
                            if state["tts_first_chunk"] is None:
                                state["tts_first_chunk"] = now_ms()
                                timeline.mark("tts_start", state["tts_first_chunk"])
                            await emit({"type": "audio_start", "seq": seq, "sample_rate": rate, "format": "pcm16", "text": cached["text"]})
                            if not state["first_audio"]:
                                state["first_audio"] = True
                                timeline.mark("first_audio", now_ms())
                            step = max(2, int(rate * 0.1) * 2)
                            pcm = cached["pcm"]
                            for i in range(0, len(pcm), step):
                                piece = pcm[i : i + step]
                                if piece:
                                    await emit({"type": "audio", "seq": seq, "pcm": piece})
                            await emit({"type": "audio_end", "seq": seq})
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
                "first_reply_audio_after_speech_ms": round(timeline.since_speech_end("tts_start"), 1) if state["tts_first_chunk"] else None,
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
                "first_reply_audio_after_speech_ms": metrics["first_reply_audio_after_speech_ms"],
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
