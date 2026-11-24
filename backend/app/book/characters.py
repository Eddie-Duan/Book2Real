"""绘本人物名识别：用于语音问答的主语校验，防止把 A 的情绪/台词安到 B 头上。"""
from __future__ import annotations

import re

from ..utils import normalize_text

# 当前 6 本赛题绘本的主要人物/动物。新书可以在 book.json 里加 characters 扩充。
KNOWN_PEOPLE: dict[str, tuple[str, ...]] = {
    "小满": ("小满", "xiaoman", "xiao man"),
    "花花": ("花花", "huahua", "hua hua"),
    "黄米团": ("黄米团", "huangmituan", "huang mi tuan"),
    "妈妈": ("妈妈", "mom", "mother", "mum"),
    "爸爸": ("爸爸", "dad", "father"),
    "乐乐": ("乐乐", "lele"),
    "查理": ("查理", "charlie"),
    "Kuan": ("kuan",),
}

QUOTE_RE = re.compile(r"[“\"「『](.*?)[”\"」』]", re.S)
_CONTRAST = ("不是", "没有", "没写", "没说明", "没有说明", "而是", "其实", "只写", "原文", "但", "不过")


def names_in(text: str) -> set[str]:
    """返回文本中出现的规范人物名。英文按词边界，中文按子串。"""
    low = (text or "").lower()
    found: set[str] = set()
    for canonical, aliases in KNOWN_PEOPLE.items():
        for alias in aliases:
            a = alias.lower()
            if a.isascii():
                if re.search(r"(?<![a-z])" + re.escape(a) + r"(?![a-z])", low):
                    found.add(canonical)
                    break
            elif a in (text or ""):
                found.add(canonical)
                break
    return found


def strip_quotes(text: str) -> str:
    return QUOTE_RE.sub("", text or "")


def quote_spans(text: str) -> list[str]:
    return [m.group(1) for m in QUOTE_RE.finditer(text or "")]


def only_in_quote(answer: str, passages: list[dict]) -> bool:
    """答案是否只是原文引号里的一句台词（没有叙述事实支撑）。"""
    a = normalize_text(answer)
    if not a:
        return False
    for p in passages or []:
        text = str(p.get("page_text") or p.get("text") or "")
        narration = normalize_text(strip_quotes(text))
        for q in quote_spans(text):
            nq = normalize_text(q)
            if not nq:
                continue
            if a in nq:
                return True
            if nq in a:
                # 引号内容只是答案的一部分：如果引号外还有叙述性内容，放行
                extra = a.replace(nq, "")
                if len(extra) >= 4 and any(w in extra for w in ("说", "问", "回答", "告诉", "怪", "因为", "所以", "可是", "花花", "小满", "生气", "开心", "难过")):
                    continue
                if a not in narration:
                    return True
    return False


def has_contrast(text: str) -> bool:
    return any(w in (text or "") for w in _CONTRAST)


def is_person_question(query: str) -> bool:
    return bool(names_in(query))


def is_who_question(query: str) -> bool:
    q = (query or "").lower()
    return "谁" in q or re.search(r"\bwho\b", q) is not None
