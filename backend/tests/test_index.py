from __future__ import annotations

from app.book.index import BookIndex, route_intent
from app.book.ingest import book_id_from_name, parse_script
from app.book.aliases import expand_query


def _book():
    return {
        "id": "demo",
        "title": "演示绘本",
        "lang": "zh",
        "page_count": 3,
        "pages": [
            {"no": 1, "story_text": "小满和花花一起搭积木城堡。", "teaching_text": "", "blocks": [{"text": "积木城堡", "bbox": [0.1, 0.1, 0.3, 0.2], "kind": "story"}]},
            {"no": 2, "story_text": "积木城堡倒了，花花很生气。", "teaching_text": "说一说，积木城堡为什么倒了？", "blocks": [{"text": "积木城堡倒了", "bbox": [0.2, 0.2, 0.5, 0.3], "kind": "story"}, {"text": "积木城堡为什么倒了", "bbox": [0.2, 0.9, 0.6, 0.95], "kind": "teaching"}]},
            {"no": 3, "story_text": "她们又一起把城堡搭好了。", "teaching_text": "", "blocks": []},
        ],
        "elements": [{"name": "积木城堡", "page": 1, "bbox": [0.1, 0.1, 0.3, 0.2], "info": "小满和花花一起搭的城堡。"}],
        "script": {"items": [{"no": 1, "text": "播放", "type": "playback", "page": 1}]},
    }


def test_search_excludes_script():
    idx = BookIndex(_book())
    hits = idx.search("积木城堡")
    assert hits
    assert hits[0]["page"] in (1, 2)
    assert hits[0]["kind"] != "script"


def test_find_text_story_first():
    idx = BookIndex(_book())
    hits = idx.find_text("积木城堡")
    assert hits
    assert hits[0]["kind"] == "story"
    assert hits[0]["page"] == 1


def test_route_playback_and_highlight():
    idx = BookIndex(_book())
    r = route_intent("暂停", idx)
    assert r["intent"] == "playback"
    assert r["action"]["type"] == "pause"
    r2 = route_intent("高亮“积木城堡”", idx)
    assert r2["intent"] == "find_text"
    assert r2["action"]["page"] == 1


def test_cross_language_alias():
    idx = BookIndex(_book())
    r = route_intent("Where is Xiaoman?", idx)
    assert r["intent"] == "find_scene"
    assert r.get("action", {}).get("page") == 1
    assert "小满" in expand_query("Where is Xiaoman?", "zh")


def test_parse_script_click_and_required():
    raw = """01. 播放 （必选）
02. 找到积木城堡的画面
    [点击] 点击积木城堡 | 暂停并展示城堡信息 （必选）
03. 积木城堡是什么颜色？
"""
    script = parse_script(raw)
    assert script["items"][0]["required"] is True
    assert script["items"][1]["type"] == "click"
    assert script["items"][1]["click"]["target"] == "积木城堡"
    assert script["items"][2]["type"] == "question"


def test_book_id_from_name():
    assert book_id_from_name("中文绘本01") == ("cn01", "zh")
    assert book_id_from_name("英文绘本03") == ("en03", "en")


def test_alias_word_boundary_does_not_match_car_in_scared():
    ex = expand_query("Who is scared?", "en")
    assert "汽车" not in ex
    assert "afraid" in ex


def test_english_stopwords_and_plural_stemming():
    book = {
        "id": "en-demo",
        "title": "Demo",
        "lang": "en",
        "page_count": 2,
        "pages": [
            {"no": 1, "story_text": "Kuan has been afraid to see films. Anything scary makes him cry.", "teaching_text": "", "blocks": []},
            {"no": 2, "story_text": "The cinema sells food, popcorn and snacks.", "teaching_text": "", "blocks": []},
        ],
        "elements": [],
        "script": {"items": []},
    }
    idx = BookIndex(book)
    r = route_intent("Who is scared?", idx, current_page=1)
    assert r["passages"][0]["page"] == 1
    r2 = route_intent("What food is there?", idx, current_page=2)
    assert r2["passages"][0]["page"] == 2


def test_where_is_prefers_current_page():
    idx = BookIndex(_book())
    r = route_intent("Where is Xiaoman?", idx, current_page=1)
    assert r["intent"] == "find_scene"
    assert r["action"]["page"] == 1
