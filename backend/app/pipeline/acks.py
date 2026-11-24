"""不承诺内容的垫话：先秒回一句拖住，真答案在后面校验通过才播。"""
from __future__ import annotations

import random

ZH_ACKS: tuple[str, ...] = (
    "好的，我看一下。",
    "让我翻翻这一页。",
    "嗯，我看看书里怎么说。",
    "我找找看。",
    "这个问题有意思，我确认一下。",
    "别急，我看看。",
    "我先看看画面。",
    "我翻一下书。",
    "嗯，让我想一想。",
    "好的，马上告诉你。",
    "我先对一对页码。",
    "我看看是谁。",
    "让我确认一下人物。",
    "我再看一眼。",
    "马上就好。",
    "我找一下这一页。",
    "嗯，我查查。",
    "好的，我先看看原书。",
    "让我找找线索。",
    "我确认一下再回答。",
    "这个我得看一眼。",
    "我先看看图。",
    "嗯，我核对一下。",
    "好的，我翻一下。",
    "让我看看前后页。",
    "我先找找这句话。",
    "我看看故事里怎么写的。",
    "好的，我理一理。",
    "别急，我马上找。",
    "我看看是谁说的。",
    "让我再看清楚一点。",
    "好的，我仔细看看。",
)

EN_ACKS: tuple[str, ...] = (
    "Okay, let me take a look.",
    "Let me check this page.",
    "Hmm, let me see what the book says.",
    "Let me find it.",
    "Good question, let me check.",
    "One moment, let me look.",
    "Let me look at the picture.",
    "Let me flip through the book.",
    "Hmm, let me think.",
    "Okay, I'll tell you in a second.",
    "Let me check the page.",
    "Let me see who it is.",
    "Let me confirm the character.",
    "Let me take another look.",
    "Just a moment.",
)


def pick(lang: str, recent: list[str] | None = None) -> str:
    bank = EN_ACKS if (lang or "").lower().startswith("en") else ZH_ACKS
    recent = recent or []
    choices = [x for x in bank if x not in recent[-6:]] or list(bank)
    return random.choice(choices)
