from __future__ import annotations

from app.book.index import BookIndex
from app.pipeline.answer_guard import contrast_answer, needs_verification, safe_answer, validate_answer
from app.pipeline.turn import build_messages


def test_character_question_needs_verification():
    assert needs_verification("小满为什么难过？")
    assert needs_verification("谁收到生日卡片？")
    assert not needs_verification("积木城堡是什么颜色？")


def test_reject_wrong_subject():
    ok, why = validate_answer(
        "小满为什么难过？",
        "花花的生日到了，可是花花一点儿也不开心。想和小满一起过生日。",
        passages=[{"page_text": "花花的生日到了，可是花花一点儿也不开心。“想和小满一起过生日。”"}],
    )
    assert not ok
    assert why.startswith("subject_swap")


def test_reject_quote_only():
    ok, why = validate_answer(
        "小满为什么生气？",
        "小满，都怪你！",
        passages=[{"page_text": "高高积木倒一地，花花很生气。“小满，都怪你！”"}],
    )
    assert not ok
    assert why == "quote_only"


def test_accept_contrast_answer():
    ok, _ = validate_answer(
        "小满为什么难过？",
        "原文没写小满为什么难过，只写花花生气并怪小满。",
    )
    assert ok


def test_accept_correct_who_answer():
    ok, _ = validate_answer("谁收到生日卡片？", "花花收到小满画的生日卡片。")
    assert ok


def test_reject_who_without_person():
    ok, why = validate_answer("谁收到生日卡片？", "生日卡片真好看！")
    assert not ok
    assert why == "who_missing"


def test_safe_answer_asks_clarification_for_two_people():
    text = safe_answer("小满和花花为什么吵架？")
    assert "小满" in text and "花花" in text


def test_llm_prompt_contains_character_lock_rules():
    book = {
        "title": "一起搭积木吧",
        "lang": "zh",
        "pages": [{"no": 6, "story_text": "花花很生气。“小满，都怪你！”"}],
        "elements": [],
    }
    messages = build_messages("小满为什么生气？", book, {"intent": "qa", "lang": "zh"}, [], 6)
    system = messages[0]["content"]
    assert "严禁把一个人物" in system
    assert "小满" in system and "花花" in system
    assert "引号里的话是角色说的" in system
    assert "绘本没有明说" in system


def test_asr_initial_prompt_contains_character_names():
    idx = BookIndex(
        {
            "id": "demo",
            "title": "演示绘本",
            "lang": "zh",
            "pages": [{"no": 1, "story_text": "小满和花花一起搭积木。"}],
            "elements": [],
            "script": {"items": []},
        }
    )
    prompt = idx.asr_initial_prompt()
    assert "小满" in prompt and "花花" in prompt


def test_contrast_answer_for_wrong_subject_emotion():
    passages = [{"page_text": "花花的生日到了，可是花花一点儿也不开心。“想和小满一起过生日。”"}]
    text = contrast_answer("小满为什么难过？", passages)
    assert text is not None
    assert text.startswith("不是小满，是花花")
    ok, _ = validate_answer("小满为什么难过？", text, passages=passages)
    assert ok


def test_contrast_answer_none_for_who_question():
    passages = [{"page_text": "花花的生日到了，可是花花一点儿也不开心。"}]
    assert contrast_answer("谁收到生日卡片？", passages) is None
