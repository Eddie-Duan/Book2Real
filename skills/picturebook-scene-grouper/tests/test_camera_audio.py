"""摄影机语言 + 声音计划的规则测试（纯标准库）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_audio_plan as ap  # noqa: E402
import camera_language as cl  # noqa: E402


def test_first_shot_establishing_and_last_closes():
    cams = cl.plan_camera(3, 1)
    assert cams[0]["shot_size"] == "LS" and cams[0]["move"] == "static"
    assert cams[-1]["shot_size"] == "CU" and cams[-1]["move"] == "static"


def test_all_moves_are_static():
    for cam in cl.plan_camera(6, 2):
        assert cam["move"] == "static"


def test_no_two_consecutive_same_shot_size():
    cams = cl.plan_camera(6, 2)
    for a, b in zip(cams, cams[1:]):
        assert a["shot_size"] != b["shot_size"]


def test_camera_sentence_locks_camera_and_demands_full_action():
    cam = cl.plan_camera(1, 1)[0]
    s = cl.camera_sentence(cam, has_characters=True)
    assert "固定机位" in s and "不推近" in s
    assert "人物动作完整可见" in s and "不放慢" in s


def test_audio_plan_maps_page_001_to_book_p01():
    book = {"book_id": "t", "language": "zh",
            "pages": [{"id": "p01", "text": "第一页。"},
                      {"id": "p02", "text": "花花说：“你好！”"}]}
    plan = {"segments": [{"segment_id": "seg_001", "pages": ["page_001", "page_002"],
                          "duration_sec": 20}]}
    out = ap.build(book, plan)
    assert out["lines"], "page_001 应能映射到 book p01"
    assert out["total_sec"] == 20


def test_enrich_writes_camera_into_every_shot():
    prompts = {"segments": [{"scene_id": "sc_01", "pages": ["p01", "p02"],
                             "shots": [{"kind": "merged", "pages": ["p01", "p02"],
                                        "duration_sec": 10}], "prompt": "base"}]}
    out = cl.enrich(prompts, {"sc_01": ["huahua"]})
    seg = out["segments"][0]
    assert seg["shots"][0]["camera"]["move"] == "static"
    assert "镜头" in seg["prompt"]


def test_dialogue_split_and_speaker():
    lines = ap.split_lines("花花说：“我们一起玩吧！”", ["花花"], ["花花"])
    kinds = [l["kind"] for l in lines]
    assert "dialogue" in kinds
    dlg = [l for l in lines if l["kind"] == "dialogue"][0]
    assert dlg["speaker"] == "花花"
    assert dlg["text"] == "我们一起玩吧！"


def test_voice_cards_are_distinct_for_two_characters():
    cards = ap.voice_card("zh", ["a", "b"])
    assert cards["a"]["voice"] != cards["b"]["voice"]
    assert cards["narrator"]["role"] == "narrator"


def test_audio_plan_timing_is_monotonic():
    book = {"book_id": "t", "language": "zh",
            "pages": [{"id": "p01", "text": "第一页。", "characters": []},
                      {"id": "p02", "text": "花花说：“你好！”", "characters": [{"name": "花花"}]}]}
    plan = {"estimated_total_sec": 20,
            "segments": [{"segment_id": "sc_01", "pages": ["p01", "p02"], "duration_sec": 20}]}
    out = ap.build(book, plan)
    assert out["lines"], "应当有旁白/对白行"
    starts = [l["start_sec"] for l in out["lines"]]
    assert starts == sorted(starts)
    assert all(l["end_sec"] <= 20.01 for l in out["lines"])
    assert out["channels"]["dialogue"] >= 1
