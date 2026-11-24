"""Seedance 成片 -> 播放器时间码（reader_timeline）的契约测试。

背景：交互层（点选/查字/描述）读的是 timeline 段里的 spots/text/ocr；Seedance 产线
之前的 page_timeline 只有页与时间，接进播放器后这些能力全是空转。这里把
「分镜段 + clean_keyframe 裁边参数」翻成 film.js 认的段，并锁住坐标换算。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "living-picturebook" / "scripts"))

import book_timeline as bt  # noqa: E402


def test_page_norm_to_frame_identity_without_crop() -> None:
    nb = [0.25, 0.5, 0.1, 0.2]
    assert bt.page_norm_to_frame(nb, {}) == [0.25, 0.5, 0.35, 0.7]


def test_page_norm_to_frame_accounts_for_crop_and_pad() -> None:
    """页图 1000x500 -> 裁成 800x500 -> 补成 16:9（左右各 44.4）-> 缩放到 1920x1080。

    x: u=0.1 -> (100-100+44.4)*2.16/1920 ≈ 0.05；u=0.3 -> 0.275
    y: 竖向没裁没补，归一化不变（0.2 / 0.6）
    """
    qa = {"checks": {"crop_box": [100, 0, 900, 500],
                     "source_size": [1000, 500],
                     "keyframe_size": [1920, 1080]}}
    x0, y0, x1, y1 = bt.page_norm_to_frame([0.1, 0.2, 0.2, 0.4], qa)
    assert x0 == pytest.approx(0.05, abs=0.01)
    assert y0 == pytest.approx(0.2, abs=0.01)
    assert x1 == pytest.approx(0.275, abs=0.01)
    assert y1 == pytest.approx(0.6, abs=0.01)


def test_validate_flags_overlap_and_bad_boxes() -> None:
    bad = [{"width": 1280, "height": 720, "segments": [
        {"id": "p01", "t0": 0.0, "t1": 5.0, "art_rect": [0, 0, 1280, 720],
         "spots": [{"name": "a", "box": [0.1, 0.1, 0.2, 0.2]}]},
        {"id": "p02", "t0": 4.0, "t1": 9.0, "art_rect": [0, 0, 1280, 720],
         "spots": [{"name": "b", "box": [0.9, 0.9, 0.4, 0.4]}]},
    ]}]
    problems = bt.validate_reader_timeline(bad)
    assert any("重叠" in p for p in problems)
    assert any("越界" in p for p in problems)


def test_build_reader_segments_from_real_probe_if_present() -> None:
    """有探针产物时做一次真实结构校验；没有就跳过（CI 无 .tmp 也能过）。"""
    plan_path = REPO / ".tmp" / "skill_out_v3" / "scene_plan.json"
    pages_dir = REPO / ".tmp" / "skill_pages"
    clips = REPO / ".tmp" / "skill_out" / "relay"
    if not (plan_path.is_file() and (pages_dir / "p04").is_dir()):
        pytest.skip("没有探针产物")
    plan = bt.load_json(plan_path)
    items = bt.build_reader_segments(plan, clips, REPO / "books" / "cn01", pages_dir)
    assert items and not bt.validate_reader_timeline(items), bt.validate_reader_timeline(items)
    tl = items[0]
    assert tl["segments"][0]["t0"] == 0.0
    pages = [s["id"] for s in tl["segments"]]
    assert pages[0] == "p01" and len(pages) == 12
    assert any(s["spots"] for s in tl["segments"]), "至少一页要有可点元素"
