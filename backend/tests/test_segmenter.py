from __future__ import annotations

from app.pipeline.segmenter import ClauseSplitter, split_text


def test_first_clause_short():
    s = ClauseSplitter(max_chars=48)
    out = s.feed("九块积木。")
    assert out == ["九块积木。"]


def test_long_answer_splits_into_clauses():
    text = "这是九块积木搭成的城堡，小满和花花一起搭的。后来城堡倒了，她们又一起重新搭好。"
    out = split_text(text)
    assert len(out) >= 2
    assert "".join(out).replace("。", "") != ""
    assert all(c.strip() for c in out)


def test_english_sentence():
    text = "The castle has nine blocks. It did not fall."
    out = split_text(text)
    assert out[0].startswith("The castle")
    assert len(out) >= 2


def test_no_duplicate_text():
    text = "小满和花花一起搭积木城堡，她们玩得很开心。"
    out = split_text(text)
    joined = "".join(out)
    assert joined.count("小满") == 1
