"""答案校验：说话前先确认“回答真的对上了问题”，对不上就不播。"""
from __future__ import annotations

import re
from typing import Optional

from ..utils import normalize_text
from ..book.characters import has_contrast, is_who_question, names_in, only_in_quote, quote_spans, strip_quotes

_WHY_RE = re.compile(r"为什么|为何|怎么会|why|how come", re.I)
_WHERE_RE = re.compile(r"哪里|在哪|哪儿|where", re.I)
_PRONOUN_RE = re.compile(r"它(?!们)|他(?!们)|她(?!们)|这个|这张|这句|这只|那|(?<![a-z])(it|he|she|they|this|that|these|those)(?![a-z])", re.I)
_COLOR_RE = re.compile(r"什么颜色|什么色|颜色|colour|color", re.I)
_COUNT_RE = re.compile(r"几个|多少|几块|几岁|how many", re.I)
_CAUSE = ("因为", "所以", "于是", "都怪", "结果", "想", "要", "没写", "没说明", "没有说明", "没有说", "没有明说", "明说", "只写", "写的是", "because", "so ", "since", "that's why")
_COLORS = ("红", "橙", "黄", "绿", "蓝", "紫", "白", "黑", "粉", "棕", "灰", "red", "orange", "yellow", "green", "blue", "purple", "white", "black", "pink", "brown", "grey", "gray")
_PAGE_RE = re.compile(r"第\s*\d+\s*页|\d+\s*页|page\s*\d+", re.I)
_UNCERTAIN = ("没写", "没有写", "没说明", "没有说明", "没有说", "没有明说", "明说", "不清楚", "不确定", "没看准", "没找到", "不知道", "not sure", "doesn't say", "didn't say", "no mention")
_SAFE_STALLS = (
    "这一页我还没看准，我再看看。",
    "我怕说错，让我再确认一下。",
    "我先对一下画面，别急。",
    "我再看一眼，马上告诉你。",
)

_EMOTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("难过", ("难过", "不开心", "伤心", "哭", "不高兴")),
    ("生气", ("生气", "愤怒", "都怪")),
    ("开心", ("开心", "高兴", "快乐")),
    ("害怕", ("害怕", "怕")),
)
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*")


def contrast_answer(query: str, passages: Optional[list[dict]]) -> str | None:
    """问 A 的情绪、原文只写 B 时，生成“不是A，是B……”的纠正句；不靠模型自由发挥。"""
    qnames = names_in(query)
    if len(qnames) != 1:
        return None
    a_name = next(iter(qnames))
    emotion = None
    for key, words in _EMOTIONS:
        if key in query or any(w in query for w in words):
            emotion = (key, words)
            break
    if emotion is None:
        return None
    _, words = emotion
    for p in passages or []:
        text = str(p.get("page_text") or p.get("text") or "")
        sents = [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]
        for i, sent in enumerate(sents):
            narration = strip_quotes(sent)
            others = names_in(narration) - {a_name}
            if not others or a_name in narration:
                continue
            hit = next((w for w in words if w in sent), None)
            if not hit:
                continue
            b_name = sorted(others)[0]
            answer = f"不是{a_name}，是{b_name}{hit}"
            if i + 1 < len(sents):
                nxt = sents[i + 1].strip().strip("“”\"'")
                if nxt and not nxt.startswith(a_name) and any(
                    w in nxt for w in ("想", "因为", "所以", "要", "一起", "见面", "生日", "和好", "对不起")
                ):
                    answer += f"，{nxt.rstrip('。！？!?')}"
            return answer if answer.endswith(("。", "！", "？", "!", "?")) else answer + "。"
    return None


def needs_verification(query: str, intent: Optional[dict] = None) -> bool:
    """人物/关系/情绪/指代类问题必须校验；普通事实问答可以继续走快路径。"""
    q = query or ""
    if names_in(q):
        return True
    if is_who_question(q):
        return True
    if _PRONOUN_RE.search(q):
        return True
    if any(w in q for w in ("难过", "伤心", "生气", "开心", "高兴", "哭", "笑", "害怕", "吵架", "和好", "道歉", "送", "收到", "喜欢", "讨厌")):
        return True
    return False


def validate_answer(
    query: str,
    answer: str,
    intent: Optional[dict] = None,
    passages: Optional[list[dict]] = None,
) -> tuple[bool, str]:
    """返回 (是否可播, 原因)。宁可 fail 让上层拖时间，也不放错答案。"""
    a = (answer or "").strip()
    if len(normalize_text(a)) < 3:
        return False, "empty"

    # 去掉“绘本里说：”这类前缀再判断内容
    core = re.sub(r"^(绘本里说|书上说|the book says)\s*[:：]?\s*", "", a, flags=re.I).strip()
    qnames = names_in(query)
    anames = names_in(core)

    if passages and only_in_quote(core, passages):
        return False, "quote_only"

    if qnames:
        missing = qnames - anames
        if missing:
            return False, f"subject_missing:{','.join(sorted(missing))}"
        others = anames - qnames
        if others and not has_contrast(core):
            # 允许“花花说：‘小满，都怪你’”这种：引号是台词，引号外叙述点名了问题人物
            if not (quote_spans(core) and (qnames & names_in(strip_quotes(core)))):
                return False, f"subject_swap:{','.join(sorted(others))}"

    if _WHY_RE.search(query):
        if not any(w in core.lower() for w in _CAUSE):
            negated = any(w in core for w in ("不是", "没有", "没写", "没说明", "没有明说"))
            corrected = any(w in core for w in ("是", "生气", "难过", "开心", "哭", "害怕", "只写", "写的是"))
            if not (negated and corrected):
                return False, "no_cause"

    if is_who_question(query):
        if not anames and not any(w in core for w in _UNCERTAIN):
            return False, "who_missing"

    if _WHERE_RE.search(query):
        if not _PAGE_RE.search(core) and not any(w in core for w in ("这里", "那里", "画面", "page")):
            return False, "no_location"

    if _COLOR_RE.search(query):
        if not any(w in core.lower() for w in _COLORS):
            return False, "no_color"

    if _COUNT_RE.search(query):
        if not re.search(r"\d|[一二三四五六七八九十百]|one|two|three|four|five|six|seven|eight|nine|ten", core, re.I):
            return False, "no_count"

    return True, "ok"


def safe_answer(query: str) -> str:
    """校验失败时的兜底：宁可澄清/拖时间，也不给错答案。"""
    names = sorted(names_in(query))
    if len(names) >= 2:
        return f"你问的是{names[0]}还是{names[1]}？"
    if len(names) == 1:
        return f"这一页我没看准{names[0]}的事，我再看看。"
    if is_who_question(query):
        return "我得再确认一下是谁，别急。"
    return "这一页我还没看准，我再看看。"
