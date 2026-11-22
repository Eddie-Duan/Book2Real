"""scene-grouper 的规则测试（纯标准库，不碰模型）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import group_scenes as gs  # noqa: E402


def book(pages):
    return {"book_id": "t", "language": "zh", "pages": pages}


def page(pid, text, chars=None):
    return {"id": pid, "text": text, "characters": chars or []}


def test_continuation_page_stays_in_scene():
    b = book([
        page("p01", "第一句话没有结束，"),
        page("p02", "接着说完了。"),
    ])
    plan = gs.build(b)
    assert plan["scene_count"] == 1
    assert plan["segments"][0]["pages"] == ["p01", "p02"]


def test_time_cue_splits_scene():
    b = book([
        page("p01", "他们在屋里搭积木。"),
        page("p02", "第二天，他们去了公园。"),
    ])
    plan = gs.build(b)
    assert plan["scene_count"] == 2
    assert plan["boundaries"][0]["signals"][0].startswith("时间/地点转换词")


def test_scene_cap_is_four_pages():
    b = book([page(f"p{i:02d}", f"第{i}页讲了一件事。") for i in range(1, 7)])
    plan = gs.build(b)
    assert all(len(s["pages"]) <= gs.MAX_PAGES_PER_SCENE for s in plan["segments"])


def test_text_coverage_is_100_percent():
    b = book([page("p01", "一句话。"), page("p02", "第二句。"), page("p03", "")])
    plan = gs.build(b)
    assert plan["coverage"]["pages_assigned"] == plan["coverage"]["pages_total"] == 3
    assert plan["coverage"]["text_pct"] == 1.0


def test_duration_lands_in_official_band():
    b = book([page(f"p{i:02d}", f"第{i}句。") for i in range(1, 13)])
    plan = gs.build(b)
    assert 100 <= plan["estimated_total_sec"] <= 200


def test_shot_duration_is_5_or_10():
    b = book([page("p01", "一。"), page("p02", "二。"), page("p03", "三。")])
    plan = gs.build(b)
    for seg in plan["segments"]:
        for shot in seg["shot_plan"]:
            assert shot["duration_sec"] in (5, 10)
