"""英文侧检索修复的回归用例（2026-11-24）。

背景：en01 官方式评只有 0.545——官方脚本问 "What sport is it?" / "What do they
discuss?" / "Is Dad awake?"，这些词在绘本正文里一个都不出现，检索直接 0 命中。
修法是「小词表同义 + 人物名 who-意图加权 + 命中词数破平局」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from book2real import evaluate as ev

REPO = Path(__file__).resolve().parents[1]


def _pages(book: str = "en01") -> list[dict]:
    data = json.loads((REPO / "books" / book / "reader" / "book.json").read_text(encoding="utf-8"))
    return [p for p in (data.get("pages") or []) if isinstance(p, dict)]


@pytest.mark.parametrize("query,want", [
    ("What sport is it?", "p25"),
    ("Is Dad awake?", "p28"),
    ("What do they discuss?", "p24"),      # 已知残留：p24 也含 talking，按命中词数破平局
    ("Who buys snacks?", "p16"),
])
def test_english_queries_land_on_the_scripted_page(query: str, want: str) -> None:
    pages = _pages()
    names = ev._character_names(REPO / "books" / "en01", pages)
    hits = ev.retrieve(pages, query, char_names=names)
    assert hits, f"{query!r} 应该至少命中一页"
    assert hits[0]["page"] == want, f"{query!r} -> {hits[0]['page']}，期望 {want}"


def test_person_names_found_without_characters_json() -> None:
    pages = _pages()
    names = ev._person_names(pages)
    assert "Kuan" in names and "Dad" in names
    # 物体名不该被当成人名
    assert "Robot" not in names and "Popcorn" not in names


def test_who_intent_does_not_reward_object_only_pages() -> None:
    pages = _pages()
    names = ev._character_names(REPO / "books" / "en01", pages)
    hits = ev.retrieve(pages, "Who is Kuan?", char_names=names)
    assert hits
    top = next(p for p in pages if p.get("id") == hits[0]["page"])
    body = " ".join([str(top.get("text") or ""), str(top.get("scene") or "")]
                    + [str(s.get("name") or "") for s in (top.get("spots") or [])])
    assert "Kuan" in body, f"who-意图投到了没有人名的页：{hits[0]}"
