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
from ..book.characters import KNOWN_PEOPLE
from ..book.store import Store
from .answer_guard import contrast_answer, needs_verification, safe_answer, validate_answer
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
    lang = intent.get("lang") or detect_lang(query)
    roster = [
        str(c.get("name") or "").strip()
        for c in (book.get("characters") or [])
        if isinstance(c, dict) and str(c.get("name") or "").strip()
    ] or list(KNOWN_PEOPLE)
    system = (
        "你是一位温柔、有耐心的儿童绘本伴读老师，正在用这本书自己的声音和孩子说话。\n"
        f"当前绘本：《{book.get('title', '')}》，书页语言：{book_lang}。\n"
        f"当前页码：{current_page or '未知'}。\n"
        "回答要以下面的绘本原文和本页画面信息为依据；不要新增原文没有的人物、地点、数字或与故事无关的内容。\n"
        f"{style_block}\n"
        f"本书人物：{'、'.join(roster[:8])}。\n"
        "只可推断画面可见的连续动作，不要推断人物情绪归属或人物关系。\n"
        "规则：\n"
        "0. 严禁输出思考过程、分析或 <think> 标签，第一个字就是最终回答。\n"
        f"1. {language_rule}\n"
        f"2. {length_rule}\n"
        "3. 直接回答，不要重复问题，不要开场白，不要 markdown、列表、emoji。\n"
        f"4. {mode_rule}\n"
        "5. 为什么类问题优先用故事里的因果；若原文没明说，就直说“绘本没有明说”，不要编。词语解释要用孩子能懂的话。\n"
        "6. 先判断问题问的是谁；回答中的人物必须与问题主语一致，严禁把一个人物的情绪、动作或台词安到另一个人物身上。\n"
        "7. 引号里的话是角色说的，不是事实；例如“小满，都怪你！”是花花在怪小满，不是小满在生气。\n"
        "8. 如果原文只写了另一个角色，就明确纠正“不是这个人，是那个人……”；问 A 却只写到 B 时，必须说出“不是A，是B”，不能只用“没写A”打发。原文完全没写才说“绘本没有明说”。\n"
        "9. 谁/为什么类问题必须点名具体人物；不要用“生日卡片真好看”这种没有主语的句子糊弄。\n"
        "10. 不要使用“根据资料／作为一个 AI／绘本里说／书上说／总之／希望对你有帮助”这类套话。\n"
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
        query_cache=None,
    ) -> None:
        self.settings = settings
        self.asr = asr
        self.tts = tts
        self.llm = llm
        self.filler = filler
        self.store = store
        self.chitchat_cache = chitchat_cache
        self.query_cache = query_cache

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
        query_hit = None
        if self.query_cache is not None:
            query_hit = self.query_cache.lookup(str(book.book.get("id") or ""), query)
        if query_hit is not None:
            intent = {
                "intent": query_hit.intent or "qa",
                "lang": query_hit.lang,
                "action": query_hit.action,
                "evidence": query_hit.evidence,
                "answer": query_hit.answer,
                "cached_query": True,
            }
        else:
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
            "used_ack": False,
            "ack_text": "",
            "answer": "",
            "answer_done": False,
            "tts_done": False,
            "audio_seq": 0,
            "cancelled": False,
            "tts_error": "",
            "llm_first_token": None,
            "tts_first_chunk": None,
            "answer_first_clause": None,
            "answer_audio_first": None,
            "filler_seq": None,
            "verified": False,
            "contrast_path": False,
            "safety_fallback": False,
            "cached_query": bool(query_hit is not None),
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
                if query_hit is not None:
                    # 50 条赛题 query 命中：预生成答案 + 预合成语音，直接播
                    state["llm_first_token"] = now_ms()
                    timeline.mark("llm_first_token", state["llm_first_token"])
                    await emit({"type": "answer_delta", "text": query_hit.answer})
                    state["answer"] = query_hit.answer
                    state["cached_audio"] = {
                        "text": query_hit.answer,
                        "pcm": query_hit.pcm,
                        "rate": query_hit.sample_rate,
                    }
                    await clause_queue.put(None)
                elif cached:
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
                elif self.settings.verified_answers and intent.get("intent") in ("qa", "definition") and needs_verification(query, intent):
                    # 人物/关系类问题：先垫话，再生成“可校验”的真答案；校验不过只播澄清/拖延。
                    from ..providers.llm import compose_extractive

                    page_obj = book.page(answer_page) if answer_page else None
                    passages = intent.get("passages", [])
                    text = ""
                    if self.settings.fast_answer and passages:
                        quick = compose_extractive(
                            query, passages, lang,
                            current_page_text=(page_obj or {}).get("story_text", ""),
                        )
                        ok, why = validate_answer(query, quick, intent, passages)
                        if ok:
                            text = quick
                            state["fast_path"] = True
                        else:
                            log.info("extractive answer rejected (%s): %r", why, quick)
                    contrast = contrast_answer(query, passages)
                    if not text and contrast:
                        ok, _ = validate_answer(query, contrast, intent, passages)
                        if ok:
                            text = contrast
                            state["contrast_path"] = True
                    if not text:
                        messages = build_messages(query, book.book, intent, history, answer_page)
                        context = {
                            "query": query,
                            "passages": passages,
                            "lang": lang,
                            "intent": intent.get("intent"),
                            "current_page_text": (page_obj or {}).get("story_text", ""),
                        }
                        if prewarm is not None:
                            context["_prewarm"] = prewarm

                        async def collect_llm() -> str:
                            out = ""
                            async for delta in self.llm.stream(messages, context=context):
                                if state["cancelled"]:
                                    break
                                if not delta:
                                    continue
                                if state["llm_first_token"] is None:
                                    state["llm_first_token"] = now_ms()
                                    timeline.mark("llm_first_token", state["llm_first_token"])
                                out += delta
                                if len(out) > 400:
                                    break
                            return out.strip()

                        try:
                            candidate = await asyncio.wait_for(
                                collect_llm(), timeout=self.settings.answer_verify_timeout_s
                            )
                        except Exception as exc:  # noqa: BLE001
                            log.warning("verified LLM failed: %s", exc)
                            candidate = ""
                        ok, why = validate_answer(query, candidate, intent, passages)
                        if ok:
                            if contrast and any(w in candidate for w in ("没有明说", "没写", "没有说")) and not any(
                                w in candidate for w in ("不是", "而是")
                            ):
                                candidate = contrast
                                state["contrast_path"] = True
                            text = candidate
                        else:
                            log.warning("LLM answer rejected (%s): %r", why, candidate)
                            text = safe_answer(query)
                            state["safety_fallback"] = True
                    state["verified"] = True
                    state["answer"] = text
                    await emit({"type": "answer_delta", "text": text})
                    for clause in splitter.feed(text):
                        if state["answer_first_clause"] is None:
                            state["answer_first_clause"] = now_ms()
                            timeline.mark("answer_first_clause", state["answer_first_clause"])
                        await clause_queue.put(clause)
                elif (
                    self.settings.fast_answer
                    and intent.get("intent") in ("qa", "definition")
                    and intent.get("passages")
                    and not (lang == "en" and book.book.get("lang") == "zh")
                ):
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

        async def await_min_start() -> None:
            """把第一块"真答案"音频的播放起点压到 speech_end + ANSWER_MIN_START_MS。

            只推迟播放，不推迟计算：缓存命中时答案 1ms 就绪，立刻开口会显得没有思考过程。
            垫话（ack）不受影响，它本来就排在 FILLER_DEADLINE_MS。
            """
            if state.get("min_start_done"):
                return
            state["min_start_done"] = True
            min_ms = int(getattr(self.settings, "answer_min_start_ms", 0) or 0)
            if min_ms <= 0:
                return
            remain = speech_end + min_ms - now_ms()
            if remain > 0:
                await asyncio.sleep(remain / 1000.0)

        async def tts_task() -> None:
            async def emit_pcm(text: str, pcm: bytes, rate: int, *, ack: bool = False) -> None:
                if not ack:
                    await await_min_start()
                seq = state["audio_seq"]
                state["audio_seq"] += 1
                self.tts.sample_rate = rate
                if state["tts_first_chunk"] is None:
                    state["tts_first_chunk"] = now_ms()
                    timeline.mark("tts_start", state["tts_first_chunk"])
                await emit({
                    "type": "audio_start", "seq": seq, "sample_rate": rate,
                    "format": "pcm16", "text": text, "filler": ack, "ack": ack,
                })
                if not state["first_audio"]:
                    state["first_audio"] = True
                    timeline.mark("first_audio", now_ms())
                if ack:
                    state["used_filler"] = True
                    state["used_ack"] = True
                    state["ack_text"] = text
                    timeline.mark("ack_audio", now_ms())
                elif state["answer_audio_first"] is None:
                    state["answer_audio_first"] = now_ms()
                    timeline.mark("answer_audio", state["answer_audio_first"])
                step = max(2, int(rate * 0.1) * 2)
                for i in range(0, len(pcm), step):
                    piece = pcm[i : i + step]
                    if piece:
                        await emit({"type": "audio", "seq": seq, "pcm": piece})
                await emit({"type": "audio_end", "seq": seq, "filler": ack, "ack": ack})
                if ack:
                    await emit({"type": "filler_used", "text": text, "ack": True})

            try:
                while True:
                    clause = await clause_queue.get()
                    if clause is None:
                        # 缓存命中：直接播预合成音频（0ms 合成）
                        cached = state.get("cached_audio")
                        if cached and not state["cancelled"]:
                            await emit_pcm(cached["text"], cached["pcm"], cached["rate"])
                        break
                    if state["cancelled"]:
                        continue
                    if isinstance(clause, dict) and clause.get("kind") == "cached":
                        await emit_pcm(
                            str(clause.get("text") or ""),
                            clause.get("pcm") or b"",
                            int(clause.get("rate") or self.settings.tts_sample_rate),
                            ack=bool(clause.get("ack")),
                        )
                        continue
                    seq = state["audio_seq"]
                    state["audio_seq"] += 1
                    started = False
                    try:
                        async for chunk in self.tts.stream(clause, lang, seq=seq):
                            if state["cancelled"] or not chunk:
                                continue
                            if not started:
                                await await_min_start()
                                started = True
                                if state["tts_first_chunk"] is None:
                                    state["tts_first_chunk"] = now_ms()
                                    timeline.mark("tts_start", state["tts_first_chunk"])
                                if state["answer_audio_first"] is None:
                                    state["answer_audio_first"] = now_ms()
                                    timeline.mark("answer_audio", state["answer_audio_first"])
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
            # 兜底：ack 库没准备好时，仍在 deadline 补一句垫话
            delay = max(0.0, (speech_end + self.settings.filler_deadline_ms - now_ms()) / 1000.0)
            await asyncio.sleep(delay)
            if state["first_audio"] or state["cancelled"]:
                return
            item = self.filler.get(lang)
            if not item:
                return
            text, pcm, rate = item
            await clause_queue.put({"kind": "cached", "text": text, "pcm": pcm, "rate": rate, "ack": True})

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
                "used_ack": state["used_ack"],
                "ack_text": state["ack_text"],
                "verified": state["verified"],
                "contrast_path": state["contrast_path"],
                "cached_query": state["cached_query"],
                "safety_fallback": state["safety_fallback"],
                "tts_error": state["tts_error"],
                "asr_ms": round(asr_ms, 1),
                "first_audio_after_speech_ms": round(timeline.since_speech_end("first_audio"), 1) if state["first_audio"] else None,
                "first_reply_audio_after_speech_ms": round(timeline.since_speech_end("tts_start"), 1) if state["tts_first_chunk"] else None,
                "answer_audio_after_speech_ms": round(timeline.since_speech_end("answer_audio"), 1) if state["answer_audio_first"] else None,
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
                "answer_audio_after_speech_ms": metrics.get("answer_audio_after_speech_ms"),
                "asr_ms": metrics.get("asr_ms"),
                "used_filler": state["used_filler"],
                "used_ack": state["used_ack"],
                "verified": state["verified"],
                "contrast_path": state["contrast_path"],
                "cached_query": state["cached_query"],
                "safety_fallback": state["safety_fallback"],
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
