from __future__ import annotations

import asyncio

from app.pipeline.turn import build_messages
from app.providers.llm import ExtractiveLLM
from app.skill4 import (
    build_style_card,
    style_fallback_text,
    style_prompt,
    style_touch_text,
    validate_answer,
)


def make_book() -> dict:
    return {
        "title": "一起搭积木吧",
        "lang": "zh",
        "pages": [
            {"no": 1, "story_text": "下雨天，花花和小满在屋里搭积木。她们一块一块往上加。"},
            {"no": 2, "story_text": "小满打了个大喷嚏，积木城堡哗啦一下倒了。花花很生气。"},
            {"no": 3, "story_text": "后来，小满画了一张生日卡片，黄米团叼着卡片跑了出去。"},
        ],
        "elements": [
            {"name": "积木城堡", "info": "用一块块彩色积木搭起来的高塔，有三角形屋顶。", "page": 2}
        ],
    }


def test_style_card_from_book_text():
    card = build_style_card(make_book())
    assert card["lang"] == "zh"
    assert card["pov"] in ("第一人称", "第三人称")
    assert card["avg_sentence_len"] > 0
    assert card["unit"] == "字"
    assert "哗啦" in card["onomatopoeia"]
    assert card["samples"]
    assert "叙述声音" in style_prompt(card, answer_lang="zh", mode="qa")


def test_qa_prompt_contains_style_and_language_rule():
    book = make_book()
    intent = {
        "intent": "qa",
        "lang": "zh",
        "passages": [{"page": 2, "text": "小满打了个大喷嚏，积木城堡哗啦一下倒了。"}],
        "evidence": [],
    }
    messages = build_messages("为什么城堡倒了？", book, intent, [], 2)
    system = messages[0]["content"]
    assert "这本书的叙述声音" in system
    assert "中文问中文答" in system
    assert "不要新增原文没有的人物" in system
    assert "哗啦" in system
    assert messages[-1]["content"] == "为什么城堡倒了？"


def test_touch_prompt_uses_book_language_and_element():
    book = make_book()
    intent = {
        "intent": "touch",
        "lang": "en",
        "answer": "用一块块彩色积木搭起来的高塔。",
        "evidence": [{"element": {"name": "积木城堡", "info": "用一块块彩色积木搭起来的高塔。"}}],
    }
    messages = build_messages("What is this?", book, intent, [], 2)
    system = messages[0]["content"]
    assert "点击画面物体" in system
    assert "使用绘本语言（zh）" in system
    assert "积木城堡" in system
    assert "先回答" not in system or "点击回答最多 1 句" in system


def test_style_touch_text_shortens_and_removes_meta():
    text = "根据资料，积木城堡是用一块块彩色积木搭起来的高塔，有三角形屋顶，还有很多小窗户，孩子们都很喜欢它。"
    out = style_touch_text(text, "zh")
    assert "根据资料" not in out
    assert len(out) <= 31


def test_style_fallback_strips_book_says():
    out = style_fallback_text(
        "绘本里说：小满打了个大喷嚏，积木城堡哗啦一下倒了。",
        answer_lang="zh", mode="qa",
    )
    assert "绘本里说" not in out
    assert "小满" in out


def test_extractive_llm_uses_style_context():
    llm = ExtractiveLLM()

    async def collect():
        return "".join([
            chunk async for chunk in llm.stream(
                [{"role": "user", "content": "为什么城堡倒了？"}],
                context={
                    "query": "为什么城堡倒了？",
                    "lang": "zh",
                    "style": True,
                    "passages": [{"page": 5, "text": "小满打了个大喷嚏，积木城堡哗啦一下倒了。"}],
                },
            )
        ])

    out = asyncio.run(collect())
    assert "绘本里说" not in out
    assert "喷嚏" in out


def test_validate_answer_reports_meta_long_and_wrong_language():
    assert validate_answer(
        "根据资料，城堡是因为喷嚏倒的。",
        answer_lang="zh", mode="qa",
    )["ok"] is False
    assert validate_answer(
        "这是一段非常长的回答，" * 8,
        answer_lang="zh", mode="qa",
    )["ok"] is False
    assert validate_answer(
        "The castle fell because Xiaoman sneezed.",
        answer_lang="zh", mode="qa",
    )["ok"] is False
    assert validate_answer(
        "小满打了个喷嚏，城堡哗啦一下倒了。",
        answer_lang="zh", mode="qa",
    )["ok"] is True
