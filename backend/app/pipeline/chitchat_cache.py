"""闲聊预热缓存：高频"测试麦克风/打招呼"问句在启动时用 LLM 生成真答案并预合成 TTS。

命中缓存 → 0ms 生成 + 0ms 合成，直接播真回答（不是模板，是 LLM 生成的）。
未命中 → 走正常 LLM 流式管线（首音约 1.2–1.7s，仍在 2s 内）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..config import Settings
from ..providers.base import LLMProvider, TTSProvider
from ..utils import normalize_text

log = logging.getLogger(__name__)

# 高频闲聊/测试句（归一化后匹配；ASR 标点差异不影响命中）
SEED_QUERIES = [
    "你能听见我说话吗",
    "能听到我说话吗",
    "你听得到吗",
    "我说话有声音吗",
    "听得到我说话吗",
    "你能听到吗",
    "你好",
    "你好呀",
    "在吗",
    "你是谁",
    "你叫什么名字",
]

_SYSTEM = (
    "你是儿童绘本伴读助手“纸书智伴”。孩子现在问的是与绘本内容无关的话（打招呼、测试麦克风/声音、闲聊）。"
    "用孩子提问的语言，亲切自然地直接回答，1-2 句，不超过 40 个字；不要提绘本原文，不要 markdown、列表、emoji。"
    "如果孩子在测试声音/麦克风，就确认你听得到并简单回应。"
)


class ChitchatCache:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # key -> {"text": str, "pcm": bytes, "rate": int, "lang": str}
        self.entries: dict[str, dict] = {}
        self.prepared = False

    def lookup(self, query: str) -> Optional[dict]:
        return self.entries.get(normalize_text(query))

    async def prepare(self, llm: LLMProvider, tts: TTSProvider) -> None:
        if not self.settings.chitchat_cache:
            return
        t0 = asyncio.get_event_loop().time()
        ok = 0
        for query in SEED_QUERIES:
            key = normalize_text(query)
            if key in self.entries:
                continue
            try:
                text = ""
                async for delta in llm.stream(
                    [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": query + "？"}]
                ):
                    text += delta
                text = text.split("</think>")[-1].strip()
                if not text:
                    continue
                pcm = await tts.synthesize(text, "zh")
                if not pcm:
                    continue
                self.entries[key] = {
                    "text": text,
                    "pcm": pcm,
                    "rate": getattr(tts, "sample_rate", 24000) or 24000,
                    "lang": "zh",
                }
                ok += 1
            except Exception as exc:  # noqa: BLE001
                log.info("chitchat cache skip %r: %s", query, exc)
        self.prepared = bool(self.entries)
        log.info("chitchat cache ready: %d entries in %.1fs", ok, asyncio.get_event_loop().time() - t0)

    def status(self) -> dict:
        return {"prepared": self.prepared, "entries": len(self.entries)}
