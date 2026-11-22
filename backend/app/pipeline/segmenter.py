"""把 LLM 流式输出切成适合 TTS 的小句：首块尽量短，后续按标点。"""
from __future__ import annotations

import re

from ..utils import clean_for_tts

_PUNCT = "，,。！？；;、.!?…\n"
_SENT_END = "。！？.!?\n"
_SPACE_RE = re.compile(r"\s+")


def _units(text: str) -> int:
    text = _SPACE_RE.sub("", text or "")
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return cjk + max(0, latin // 3)


class ClauseSplitter:
    """增量切句器。

    - 首块：到首个合适的标点即发，无标点时在自然边界提前切分。
    - 后续：按标点切，单块尽量 <= max_chars，避免 TTS 长句等待。
    """

    def __init__(self, max_chars: int = 48, first_max_units: int = 10, first_min_units: int = 3) -> None:
        self.max_chars = max(16, max_chars)
        self.first_max_units = first_max_units
        self.first_min_units = first_min_units
        self.buffer = ""
        self.emitted_first = False

    def feed(self, delta: str) -> list[str]:
        self.buffer += delta or ""
        out: list[str] = []
        while True:
            chunk = self._take()
            if not chunk:
                break
            out.append(chunk)
        return out

    def flush(self) -> list[str]:
        text = clean_for_tts(self.buffer)
        self.buffer = ""
        if not text:
            return []
        # 末尾没有标点时补一个句号，让 TTS 收尾自然
        if text[-1] not in "。！？.!?；;":
            text += "。"
        self.emitted_first = True
        return [text]

    def _take(self) -> str | None:
        text = self.buffer
        if not text:
            return None
        first = not self.emitted_first
        min_units = self.first_min_units if first else 6
        max_units = self.first_max_units if first else self.max_chars
        scan = min(len(text), self.max_chars * 2)
        cut = -1
        for i, ch in enumerate(text[:scan]):
            if ch in _PUNCT and _units(text[:i]) >= min_units:
                cut = i + 1
                if first or ch in _SENT_END:
                    break
                if _units(text[:cut]) >= max_units:
                    break
        if first:
            # Use an early boundary even if several tokens arrived together.
            # An ASCII word at the end of a delta may still be incomplete.
            for i, ch in enumerate(text[:self.max_chars], 1):
                if 0 < cut <= i:
                    break
                next_ch = text[i:i + 1]
                if ch.isascii() and ch.isalnum() and (not next_ch or (next_ch.isascii() and next_ch.isalnum())):
                    continue
                if _units(text[:i]) >= max_units:
                    cut = i
                    break
        elif cut < 0 and _units(text) >= max_units:
            # 没有标点：英文优先在空格切，中文按最大长度切
            cut = min(len(text), self.max_chars)
            if re.search(r"[A-Za-z]", text[:cut]):
                sp = text.rfind(" ", max(0, cut - 16), cut)
                if sp > 0:
                    cut = sp
        if cut <= 0:
            return None
        chunk = clean_for_tts(text[:cut])
        self.buffer = text[cut:].lstrip()
        if not chunk:
            return self._take()
        self.emitted_first = True
        return chunk


def split_text(text: str, max_chars: int = 48) -> list[str]:
    splitter = ClauseSplitter(max_chars=max_chars)
    out = splitter.feed(text)
    out.extend(splitter.flush())
    return [c for c in out if c.strip()]
