"""Skill 4: keep answers in the picture book's own narrative voice.

This module is deliberately pure Python: it reads the book text, builds a small
style card, and turns it into prompt text.  It never calls a model, so it is
cheap to run on every turn and safe to unit-test offline.

Two answer modes share the same card:

* ``qa``    — the child asked a question; answer in the *question* language.
* ``touch`` — the child tapped an object; answer in the *book* language.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

_CJK = re.compile(r"[一-鿿]")
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.])\s+(?=[A-Z])")

_ONOMATOPOEIA = (
    "哗啦", "咕噜", "沙沙", "呼呼", "阿嚏", "扑通", "叮咚", "吱吱",
    "汪汪", "喵喵", "哈哈", "哇", "嘭", "啪", "嗖", "呼噜",
)
_CONNECTORS = (
    "后来", "突然", "于是", "可是", "然后", "接着", "第二天", "这时",
    "after that", "suddenly", "then", "later", "the next day", "meanwhile",
)
_META = (
    "根据资料", "根据提供", "作为一个", "作为AI", "作为 AI", "总之",
    "综上所述", "希望对你有帮助", "以下是", "根据绘本原文",
    "绘本里说", "书上说", "书里说",
    "as an ai", "based on the information", "in conclusion",
    "the book says", "hope this helps", "here is", "let me explain",
)
_QUOTES = ("“", "”", "「", "」", "『", "』", '"', "'")
_WARM_WORDS = ("笑", "开心", "温暖", "抱", "喜欢", "soft", "warm", "smile", "laugh")


def _page_text(page: Any) -> str:
    if not isinstance(page, dict):
        return str(page or "")
    return str(page.get("story_text") or page.get("text") or "")


def split_sentences(text: str) -> list[str]:
    """Split on sentence punctuation; keep short fragments out of the card."""
    text = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    if not text:
        return []
    return [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]


def _is_cjk_text(text: str) -> bool:
    cjk = len(_CJK.findall(text or ""))
    latin = len(_LATIN_WORD.findall(text or ""))
    return cjk >= max(1, latin)


def _words(text: str) -> int:
    return len(_LATIN_WORD.findall(text or ""))


def _balanced_quotes(text: str) -> bool:
    pairs = (("“", "”"), ("「", "」"), ("『", "』"), ('"', '"'))
    for left, right in pairs:
        if text.count(left) != text.count(right):
            return False
    return True


def _pick_samples(sentences: Iterable[str], lang: str, n: int = 3) -> list[str]:
    """Pick short, concrete, non-question sentences as style exemplars."""
    candidates = []
    for sent in sentences:
        s = sent.strip()
        if not s or s.endswith(("？", "?")) or not _balanced_quotes(s):
            continue
        size = len(_CJK.findall(s)) if lang == "zh" else _words(s)
        if (lang == "zh" and 8 <= size <= 42) or (lang == "en" and 5 <= size <= 26):
            candidates.append(s)
    if not candidates:
        candidates = [s.strip() for s in sentences if s.strip() and _balanced_quotes(s)][:n]
    # Prefer narration over dialogue, then connectors/sound words, then a
    # natural sentence length.  A bedtime-story voice is mostly narration.
    candidates.sort(
        key=lambda s: (
            any(q in s for q in _QUOTES),
            not any(c in s.lower() for c in _CONNECTORS),
            not any(w in s for w in _ONOMATOPOEIA),
            abs(len(s) - (18 if lang == "zh" else 14)),
        )
    )
    out: list[str] = []
    for s in candidates:
        if s not in out:
            out.append(s)
        if len(out) >= n:
            break
    return out


def build_style_card(book: dict) -> dict:
    """Derive a compact, language-neutral style card from a reader book.

    The backend's book shape is ``{"title", "lang", "pages": [{"no",
    "story_text", ...}]}``; a few callers use ``text``/``language`` instead, so
    both are accepted.
    """
    pages = book.get("pages") or []
    texts = [_page_text(p) for p in pages]
    corpus = "\n".join(t for t in texts if t)
    lang = str(book.get("lang") or book.get("language") or "").lower()
    if lang not in ("zh", "en"):
        lang = "zh" if _is_cjk_text(corpus) else "en"
    sentences = split_sentences(corpus)
    if not sentences and corpus:
        sentences = [corpus.strip()]

    if lang == "zh":
        lengths = [len(_CJK.findall(s)) for s in sentences if _CJK.search(s)]
        unit = "字"
    else:
        lengths = [_words(s) for s in sentences if _LATIN_WORD.search(s)]
        unit = "词"
    lengths = [n for n in lengths if n > 0]
    avg_len = round(sum(lengths) / len(lengths), 1) if lengths else 0.0

    # POV is a property of the *narration*, not of quoted dialogue.  A first-
    # person "我" inside “……” must not turn a third-person book into a
    # first-person one (this happened on cn01's extracted page text).
    narration = re.sub(r"[“「『\"].*?[”」』\"]", "", corpus, flags=re.S)
    if not narration.strip():
        narration = corpus
    first_person = len(re.findall(r"(?<![我你他她它])我(?![们])", narration))
    third_person = len(re.findall(r"[他她]", narration))
    if lang == "en":
        first_person = len(re.findall(r"\bI\b", narration))
        third_person = len(re.findall(r"\b(?:he|she|they)\b", narration, re.I))
    pov = "第一人称" if first_person > third_person else "第三人称"

    dialogue = sum(1 for s in sentences if any(q in s for q in _QUOTES))
    dialogue_ratio = round(dialogue / max(1, len(sentences)), 2)
    sounds = [w for w in _ONOMATOPOEIA if w in corpus]
    connectors = [w for w in _CONNECTORS if w.lower() in corpus.lower()]
    tone = "温暖、具体" if any(w in corpus.lower() for w in _WARM_WORDS) else "简洁、具体"

    return {
        "title": str(book.get("title") or ""),
        "lang": lang,
        "pov": pov,
        "tone": tone,
        "avg_sentence_len": avg_len,
        "unit": unit,
        "sentence_count": len(sentences),
        "dialogue_ratio": dialogue_ratio,
        "onomatopoeia": sounds,
        "connectors": connectors,
        "samples": _pick_samples(sentences, lang),
    }


def limits_for(lang: str, mode: str = "qa") -> tuple[int, int, int]:
    """Return (max_chars, max_words, max_sentences) for the answer mode."""
    if mode == "touch":
        return (30, 20, 1)
    return (45, 30, 2)


def language_of(text: str) -> str:
    """Best-effort answer language; used by the validator and probes."""
    cjk = len(_CJK.findall(text or ""))
    latin = len(_LATIN_WORD.findall(text or ""))
    return "zh" if cjk >= latin else "en"


def style_prompt(card: dict, *, answer_lang: str, mode: str = "qa") -> str:
    """Render the style card as a compact block for the system prompt."""
    lang = card.get("lang", "zh")
    unit = card.get("unit", "字")
    samples = card.get("samples") or []
    sample_lines = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(samples)) or "  （无）"
    sounds = "、".join(card.get("onomatopoeia") or []) or "（无）"
    connectors = "、".join(card.get("connectors") or []) or "（无）"
    same_lang = (answer_lang == lang)
    if mode == "touch":
        focus = "这次是孩子点击画面物体后的回答：先用一句话说它是什么/在做什么，再补一个本页画面细节；不要百科式介绍。"
        length_rule = "点击回答最多 1 句，中文不超过 30 字，英文不超过 20 词。"
    else:
        focus = "这次是孩子读完一页后提出的问题：先回答问题，再补一个画面/动作细节。"
        length_rule = "回答最多 2 句，中文不超过 45 字，英文不超过 30 词。"
    bridge = (
        "回答语言与本书语言相同；直接模仿上面的句式、语气和具体程度。"
        if same_lang
        else "回答语言不是本书语言：不要硬译原句，只复现上面的句长、语气、具体程度；人名保留原文。"
    )
    return (
        "【这本书的叙述声音】\n"
        f"- 叙述人称：{card.get('pov', '第三人称')}\n"
        f"- 语气：{card.get('tone', '温暖、具体')}；多用动作和感官细节，少用抽象词。\n"
        f"- 平均句长：约 {card.get('avg_sentence_len', 0)} {unit}/句。\n"
        f"- 常用连接词：{connectors}；拟声词：{sounds}。\n"
        f"- 可模仿的原文句子（只学语气，不要照抄整句）：\n{sample_lines}\n"
        f"- {focus}\n"
        f"- {length_rule}\n"
        f"- {bridge}"
    )


def _trim_sentences(text: str, max_sentences: int, lang: str, max_chars: int, max_words: int) -> str:
    sentences = split_sentences(text)
    if sentences:
        text = "".join(sentences[:max_sentences]) if lang == "zh" else " ".join(sentences[:max_sentences])
    text = text.strip()
    if lang == "zh":
        if len(_CJK.findall(text)) > max_chars:
            text = text[:max_chars].rstrip("，、；：,;: ") + "。"
    else:
        words = _LATIN_WORD.findall(text)
        if len(words) > max_words:
            text = " ".join(words[:max_words]).rstrip(",;:") + "."
    return text


def style_touch_text(text: str, lang: str = "zh") -> str:
    """Fast, deterministic cleanup for the existing element-card answer.

    Click responses are already available immediately from the curated card;
    this keeps them short and removes assistant-style scaffolding without a
    model round-trip.  A model-generated touch answer can be enabled separately
    with ``STYLE_TOUCH_LLM=true``.
    """
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return text
    for phrase in _META:
        text = re.sub(re.escape(phrase) + r"[，,：:\s]*", "", text, flags=re.I)
    max_chars, max_words, max_sentences = limits_for(lang, "touch")
    return _trim_sentences(text, max_sentences, lang, max_chars, max_words)


def style_fallback_text(text: str, *, answer_lang: str, mode: str = "qa") -> str:
    """Keep the extractive fallback in the same voice and length budget.

    The old fallback prepended "绘本里说：" / "The book says: " which reads
    like a search result, not like the book.  Strip that framing and trim to
    the same limits used by the LLM answer.
    """
    text = re.sub(r"\s+", " ", (text or "")).strip()
    for prefix in ("绘本里说：", "绘本里说:", "书上说：", "书上说:",
                   "The book says: ", "The book says:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    max_chars, max_words, max_sentences = limits_for(answer_lang, mode)
    return _trim_sentences(text, max_sentences, answer_lang, max_chars, max_words)


def validate_answer(text: str, *, answer_lang: str, mode: str = "qa",
                    card: Optional[dict] = None) -> dict:
    """Check a generated answer before it is shown; returns a report dict."""
    raw = re.sub(r"\s+", " ", (text or "")).strip()
    reasons: list[str] = []
    if not raw:
        return {"ok": False, "text": "", "reasons": ["empty"]}

    low = raw.lower()
    if any(p.lower() in low for p in _META):
        reasons.append("meta_phrase")

    max_chars, max_words, max_sentences = limits_for(answer_lang, mode)
    cjk = len(_CJK.findall(raw))
    words = _words(raw)
    if language_of(raw) != answer_lang:
        reasons.append("language_mismatch")
    if answer_lang == "zh" and cjk > max_chars:
        reasons.append("too_long")
    if answer_lang == "en" and words > max_words:
        reasons.append("too_long")
    if len(split_sentences(raw)) > max_sentences:
        reasons.append("too_many_sentences")

    # A touch answer must name the clicked object (checked by the caller when
    # it has the element); a generic "这是一个东西" is not useful.
    if mode == "touch" and cjk < 2 and words < 2:
        reasons.append("too_vague")

    return {
        "ok": not reasons,
        "text": raw,
        "reasons": reasons,
        "limits": {"chars": max_chars, "words": max_words, "sentences": max_sentences},
        "card_lang": (card or {}).get("lang", answer_lang),
    }
