"""LLM provider：OpenAI 兼容流式 / 提取式兜底 / mock。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import AsyncIterator, Optional

import httpx

from ..config import Settings
from ..utils import detect_lang, normalize_for_tokens, normalize_text, truncate
from .base import LLMProvider

log = logging.getLogger(__name__)

_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.])\s+(?=[A-Z])")
_CAUSAL = ("因为", "所以", "于是", "都怪", "结果", "because", "so ", "since", "that's why")
_COLOR = ("红", "橙", "黄", "绿", "蓝", "紫", "白", "黑", "粉", "棕", "灰", "red", "orange", "yellow", "green", "blue", "purple", "white", "black", "pink", "brown", "grey", "gray")
_COUNT = ("块", "个", "只", "条", "张", "本", "岁", "米", "厘米", "元", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def split_sentences(text: str) -> list[str]:
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    return parts or [text]


def _tokens(text: str) -> set[str]:
    norm = normalize_for_tokens(text)
    tokens: set[str] = set()
    for run in re.findall(r"[a-z0-9]+", norm):
        if run in {"a", "an", "the", "is", "are", "was", "were", "who", "what", "which", "where", "why", "how", "it", "this", "that", "he", "she", "they", "we", "you", "i", "of", "to", "in", "on", "at", "for", "with", "and", "or", "but", "do", "does", "did", "can", "the"}:
            continue
        tokens.add(run)
        if len(run) > 3 and run.endswith("s") and not run.endswith("ss"):
            tokens.add(run[:-1])
        if len(run) > 4 and run.endswith("es"):
            tokens.add(run[:-2])
        if len(run) > 5 and run.endswith("ing"):
            tokens.add(run[:-3])
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", norm))
    tokens.update(cjk)
    tokens.update(cjk[i : i + 2] for i in range(max(0, len(cjk) - 1)))
    return {t for t in tokens if t}


def _overlap(query: str, sentence: str) -> float:
    q, s = _tokens(query), _tokens(sentence)
    if not q or not s:
        return 0.0
    return len(q & s) / max(1, len(q))


class ExtractiveLLM(LLMProvider):
    """不依赖外部模型的兜底：从检索到的绘本原文中抽一句最相关的回答。"""

    name = "extractive"

    async def stream(self, messages: list[dict], context: Optional[dict] = None) -> AsyncIterator[str]:
        ctx = context or {}
        query = str(ctx.get("query") or (messages[-1].get("content", "") if messages else "")).strip()
        passages = ctx.get("passages") or []
        lang = ctx.get("lang") or detect_lang(query)
        answer = compose_extractive(query, passages, lang, current_page_text=str(ctx.get("current_page_text") or ""))
        yield answer

    def status(self) -> dict:
        return {"provider": self.name}


def compose_extractive(query: str, passages: list[dict], lang: str = "zh", current_page_text: str = "") -> str:
    prefix = "绘本里说：" if lang == "zh" else "The book says: "
    # 海报/标签类短文本：当前页只有几个词时，直接把它作为答案（常见于“Which film?”）
    current = (current_page_text or "").strip()
    if current and len(current) <= 50 and not current.endswith(("？", "?")):
        if re.match(r"^(which|what|who|what's|who's|哪个|什么|谁|这是|这是什)", query.strip(), re.I):
            return prefix + current
    if not passages:
        return "这本绘本里没有找到相关的内容。" if lang == "zh" else "I couldn't find that in this picture book."
    qn = normalize_text(query)
    wants_cause = any(w in qn for w in ("为什么", "为何", "怎么会", "why", "how come"))
    wants_color = any(w in qn for w in ("颜色", "什么色", "colour", "color"))
    wants_count = any(w in qn for w in ("几个", "多少", "几块", "how many"))
    asks_quote = any(w in qn for w in ("说什么", "说了什么", "what did"))
    candidates: list[tuple[float, str, int, list[str]]] = []
    seen_pages: set[int] = set()
    for pidx, passage in enumerate(passages[:4]):
        page_no = passage.get("page")
        if page_no in seen_pages:
            continue
        seen_pages.add(page_no)
        text = str(passage.get("page_text") or passage.get("text") or "").strip()
        sentences = split_sentences(text)
        for idx, sent in enumerate(sentences):
            sent = sent.strip().strip("“”\"'")
            if not sent:
                continue
            score = _overlap(query, sent) * 3.0 - pidx * 0.12 - idx * 0.03
            sl = sent.lower()
            if wants_cause and any(w in sl for w in _CAUSAL):
                score += 1.5
            if wants_color and any(w in sl for w in _COLOR):
                score += 0.9
            if wants_count:
                score += 0.15 * sum(1 for w in _COUNT if w in sl)
            if "倒" in qn and ("没有倒" in sl or "不倒" in sl):
                score -= 2.5
            if "生气" in qn and "不生气" in sl:
                score -= 2.0
            if sent.endswith(("？", "?")) and not asks_quote:
                score -= 1.8
            if len(sent) < 4:
                score -= 0.8
            if len(sent) > 100:
                score -= 0.4
            candidates.append((score, sent, pidx, sentences))
    if not candidates:
        return "这本绘本里没有找到相关的内容。" if lang == "zh" else "I couldn't find that in this picture book."
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best, pidx, sentences = candidates[0]
    if len(best) < 4:
        for score, sent, _, _ in candidates:
            if len(sent) >= 4:
                best_score, best = score, sent
                break
    if best.endswith(("？", "?")) and not asks_quote:
        for score, sent, _, _ in candidates:
            if not sent.endswith(("？", "?")):
                best_score, best = score, sent
                break
    # why 问句：若最佳句不是原因句，尝试拼上同一页里下一句的因果/动作句
    if wants_cause and not any(w in best.lower() for w in _CAUSAL):
        try:
            idx = sentences.index(best)
        except ValueError:
            try:
                idx = next(i for i, s in enumerate(sentences) if best in s)
            except StopIteration:
                idx = -1
        if 0 <= idx < len(sentences) - 1:
            nxt = sentences[idx + 1].strip().strip("“”\"'")
            if nxt and not nxt.endswith(("？", "?")) and len(best) + len(nxt) < 100:
                best = best + nxt
    answer = best.strip()
    if len(answer) > 110:
        answer = truncate(answer, 108)
    return prefix + answer


class OpenAICompatLLM(LLMProvider):
    name = "openai-compat"

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key:
            raise ValueError("LLM_API_KEY is empty")
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=settings.llm_timeout_s, write=10.0, pool=5.0),
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            },
        )
        self.first_token_ms: float | None = None
        self.requests = 0
        self.failures = 0

    async def stream(self, messages: list[dict], context: Optional[dict] = None) -> AsyncIterator[str]:
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "stream": True,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
        }
        if "minimax" in self.settings.llm_base_url:
            # M3 系列强制 adaptive thinking；关掉可省首 token 延迟（不支持时忽略）
            payload["enable_thinking"] = False
        import time

        t0 = time.perf_counter()
        first = True
        self.requests += 1
        in_think = False
        think_buf = ""
        try:
            async with self._client.stream("POST", self.settings.llm_base_url + "/chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread())[:300]
                    raise RuntimeError(f"llm {resp.status_code}: {body!r}")
                async for line in resp.aiter_lines():
                    line = (line or "").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        # 过滤 thinking 模型的 <...> 前缀，只把正式回答流给 TTS
                        if in_think:
                            think_buf += content
                            if "</think>" in think_buf:
                                in_think = False
                                content = think_buf.split("</think>", 1)[1]
                                think_buf = ""
                            else:
                                continue
                        if "<think>" in content:
                            before, _, rest = content.partition("<think>")
                            if before.strip():
                                if first:
                                    self.first_token_ms = (time.perf_counter() - t0) * 1000
                                    first = False
                                yield before
                            if "</think>" in rest:
                                content = rest.split("</think>", 1)[1]
                            else:
                                in_think = True
                                think_buf = rest
                                continue
                        if not content:
                            continue
                        if first:
                            self.first_token_ms = (time.perf_counter() - t0) * 1000
                            first = False
                        yield content
        except Exception:
            self.failures += 1
            raise

    async def warmup(self) -> None:
        try:
            t0 = time.perf_counter()
            async for _ in self.stream([{"role": "user", "content": "你好"}]):
                break
            log.info("LLM warmup %.0f ms (first token %.0f ms)", (time.perf_counter() - t0) * 1000, self.first_token_ms or 0)
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM warmup failed: %s", exc)

    async def close(self) -> None:
        await self._client.aclose()

    def status(self) -> dict:
        return {
            "provider": self.name,
            "base_url": self.settings.llm_base_url,
            "model": self.settings.llm_model,
            "first_token_ms": round(self.first_token_ms or 0, 1),
            "requests": self.requests,
            "failures": self.failures,
        }


class MockLLM(LLMProvider):
    name = "mock-llm"

    def __init__(self, text: str = "这是测试回答。", delay_ms: float = 0.0, chunk_chars: int = 4) -> None:
        self.text = text
        self.delay_ms = delay_ms
        self.chunk_chars = max(1, chunk_chars)

    async def stream(self, messages: list[dict], context: Optional[dict] = None) -> AsyncIterator[str]:
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000.0)
        for i in range(0, len(self.text), self.chunk_chars):
            yield self.text[i : i + self.chunk_chars]
