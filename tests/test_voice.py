"""Tests for the interruption state machine (book2real/voice_state.py).

Runs without pytest and without network:

    python tests/test_voice.py

Last line prints ``N tests passed``.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from book2real import voice_state as V


def d(phase="idle", since=0, *, et, text=""):
    return V.decide({"phase": phase, "since_cancel_ms": since},
                    {"type": et, "text": text, "t_ms": 1000})


def test_constants_match_the_contract():
    assert set(V.DECISIONS) == {"speak", "cancel", "wait", "listen"}
    assert V.PHASES == ("idle", "listening", "thinking", "speaking")
    assert V.ECHO_GUARD_MS == 600
    assert V.MIN_INTERRUPT_CHARS == 2


def test_idle_final_is_committed_as_a_query():
    r = d("idle", et="final", text="这是什么？")
    assert r["decision"] == "listen"
    assert r["phase"] == "listening"
    assert r["text"] == "这是什么？"


def test_listening_final_is_committed_as_a_query():
    r = d("listening", et="final", text="它为什么难过")
    assert r["decision"] == "listen"
    assert r["text"] == "它为什么难过"


def test_speaking_long_partial_after_guard_cancels():
    r = d("speaking", since=601, et="partial", text="等一下")
    assert r["decision"] == "cancel"
    assert r["phase"] == "listening"
    assert r["reason"] == "barge_in"
    assert r["chars"] == 3


def test_echo_guard_blocks_immediate_barge_in():
    for since in (0, 120, 600):
        r = d("speaking", since=since, et="partial", text="妈妈妈妈")
        assert r["decision"] == "wait", since
        assert r["reason"] == "echo_guard", since
        assert r["phase"] == "speaking"


def test_echo_guard_boundary_is_strictly_greater_than_600():
    assert d("speaking", since=600, et="partial", text="停一下")["decision"] == "wait"
    assert d("speaking", since=601, et="partial", text="停一下")["decision"] == "cancel"


def test_one_character_partial_cannot_interrupt():
    for text in ("好", " ", "  ", "。", "\u3000"):
        r = d("speaking", since=5000, et="partial", text=text)
        assert r["decision"] == "wait", text
        assert r["reason"] == "too_short", text


def test_partial_counts_characters_after_removing_whitespace():
    r = d("speaking", since=5000, et="partial", text=" 好 的 ")
    assert r["chars"] == 2
    assert r["decision"] == "cancel"


def test_thinking_partial_cancels_barge_in():
    r = d("thinking", since=0, et="partial", text="不是")
    assert r["decision"] == "cancel"
    assert r["reason"] == "barge_in_thinking"
    assert r["phase"] == "listening"


def test_thinking_final_does_not_resubmit():
    r = d("thinking", since=0, et="final", text="我刚才说了什么")
    assert r["decision"] == "wait"
    assert r["phase"] == "thinking"
    assert r["reason"] == "already_thinking"


def test_speaking_final_interrupts_when_not_echo():
    r = d("speaking", since=900, et="final", text="我不想听这个")
    assert r["decision"] == "cancel"
    assert r["phase"] == "listening"
    assert r["reason"] == "barge_in_final"


def test_speaking_final_is_echo_guarded():
    r = d("speaking", since=10, et="final", text="页面上有只豚鼠")
    assert r["decision"] == "wait"
    assert r["reason"] == "echo_guard"


def test_answer_ready_from_thinking_speaks():
    r = d("thinking", since=0, et="answer_ready", text="这是劳拉。")
    assert r["decision"] == "speak"
    assert r["phase"] == "speaking"
    assert r["text"] == "这是劳拉。"


def test_answer_ready_from_idle_speaks():
    assert d("idle", et="answer_ready", text="答案")["decision"] == "speak"


def test_answer_ready_while_user_speaks_waits():
    r = d("listening", since=0, et="answer_ready", text="答案")
    assert r["decision"] == "wait"
    assert r["reason"] == "user_speaking"
    assert r["phase"] == "listening"


def test_answer_ready_while_already_speaking_does_not_stack():
    r = d("speaking", since=900, et="answer_ready", text="另一段")
    assert r["decision"] == "wait"
    assert r["reason"] == "already_speaking"


def test_speech_start_starts_playback_once():
    assert d("idle", et="speech_start")["decision"] == "speak"
    assert d("thinking", et="speech_start")["decision"] == "speak"
    again = d("speaking", et="speech_start")
    assert again["decision"] == "wait"
    assert again["reason"] == "already_speaking"


def test_speech_end_returns_to_listening():
    r = d("speaking", since=0, et="speech_end")
    assert r["decision"] == "listen"
    assert r["phase"] == "listening"
    outside = d("idle", et="speech_end")
    assert outside["decision"] == "wait" and outside["phase"] == "idle"


def test_unknown_event_waits():
    r = d("speaking", since=5000, et="bogus", text="东西")
    assert r["decision"] == "wait"
    assert r["reason"] == "unknown_event"


def test_invalid_inputs_degrade_to_idle():
    r = V.decide({"phase": "teleporting", "since_cancel_ms": "oops"}, None)
    assert r["decision"] == "wait"
    assert r["phase"] == "idle"
    assert V.decide(None, None)["decision"] == "wait"
    assert V.decide({}, {"type": "final", "text": ""})["decision"] == "listen"
    assert V.decide({"phase": "speaking", "since_cancel_ms": -5},
                    {"type": "partial", "text": "打断"})["decision"] == "wait"


def test_decide_does_not_mutate_its_arguments():
    state = {"phase": "speaking", "since_cancel_ms": 700, "partial": "等"}
    event = {"type": "partial", "text": "等一下", "t_ms": 5}
    before = (copy.deepcopy(state), copy.deepcopy(event))
    r = V.decide(state, event)
    assert (state, event) == before
    assert r["decision"] == "cancel"
    r["text"] = "mutated"
    assert event["text"] == "等一下"


def test_decide_is_deterministic():
    state = {"phase": "thinking", "since_cancel_ms": 0}
    event = {"type": "partial", "text": "不对"}
    assert V.decide(state, event) == V.decide(state, event)


def test_full_matrix_returns_legal_values():
    for phase in V.PHASES:
        for etype in V.EVENT_TYPES:
            r = V.decide({"phase": phase, "since_cancel_ms": 0},
                         {"type": etype, "text": "这是一句话", "t_ms": 0})
            assert r["decision"] in V.DECISIONS, (phase, etype, r)
            assert r["phase"] in V.PHASES, (phase, etype, r)
            assert isinstance(r["chars"], int) and r["chars"] >= 0
            assert isinstance(r["reason"], str) and r["reason"]


def test_race_cancel_then_echo_does_not_loop():
    # speaking -> cancel flips the caller to listening with since_cancel_ms ~= 0
    first = d("speaking", since=700, et="partial", text="等一等")
    assert first["decision"] == "cancel" and first["phase"] == "listening"
    second = V.decide({"phase": first["phase"], "since_cancel_ms": 20},
                      {"type": "partial", "text": "等一等", "t_ms": 2000})
    assert second["decision"] == "wait"
    # ...and a late answer must not talk over the user who just interrupted
    third = V.decide({"phase": first["phase"], "since_cancel_ms": 20},
                     {"type": "answer_ready", "text": "旧答案", "t_ms": 2100})
    assert third["decision"] == "wait"


def test_race_barge_in_then_final_commits_user_query():
    r = V.decide({"phase": "speaking", "since_cancel_ms": 800},
                 {"type": "partial", "text": "我想问"})
    assert r["decision"] == "cancel"
    committed = V.decide({"phase": r["phase"], "since_cancel_ms": 0},
                         {"type": "final", "text": "我想问这个是什么"})
    assert committed["decision"] == "listen"
    assert committed["text"] == "我想问这个是什么"


if __name__ == "__main__":
    ok = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print(f"ok  {name}")
    print(f"{ok} tests passed")
