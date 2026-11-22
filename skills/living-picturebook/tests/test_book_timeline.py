#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""book_timeline.py 的最小回归（纯标准库，不依赖 cv2 / pytest）。

跑法：
    python tests/test_book_timeline.py

覆盖四件事——这四件错了，交互层就会"暂停时判错页"：
1. 段内多页按页数均分，且标 estimated；
2. 段首页的 start 等于段的 start；
3. concat_plan 声明 xfade 时，时间码按"两段重叠"计算；
4. 按 t 查页的边界属于哪一侧（[start, end) 左闭右开）。
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import book_timeline as bt  # noqa: E402


def _base_dir() -> Path:
    """找一个可写的临时根目录。

    DSH 沙箱下 %TEMP% 建的目录可能是 0o700 ACL，子目录写不进去；仓库内
    `_tmp_tests` 通常可写。两个都探测一遍，避免测试在受限环境里假失败。
    """
    for cand in (HERE.parent / "_tmp_tests", Path(tempfile.gettempdir()) / "living_pb_tests"):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / f".probe_{uuid.uuid4().hex[:8]}"
            probe.mkdir()
            probe.rmdir()
            return cand
        except OSError:
            continue
    raise SystemExit("找不到可写临时目录")


BASE = _base_dir()


def _workdir() -> Path:
    d = BASE / f"t{uuid.uuid4().hex[:10]}"
    d.mkdir(parents=True)
    return d


def _plan(concat_plan=None) -> dict:
    return {
        "plan_version": "1.0.0",
        "master_ratio": "4:3",
        "pages": [
            {"page_id": "page_001"},
            {"page_id": "page_002"},
            {"page_id": "page_003"},
        ],
        "segments": [
            {
                "segment_id": "seg_001",
                "pages": ["page_001", "page_002"],
                "duration_sec": 5,
                "segment_kind": "MERGED_PAGES",
                "first_frame": {"source": "page_001/clean_keyframe.png", "kind": "keyframe"},
                "last_frame": {"source": "page_002/clean_keyframe.png", "kind": "keyframe"},
            },
            {
                "segment_id": "seg_002",
                "pages": ["page_003"],
                "duration_sec": 5,
                "segment_kind": "SINGLE_PAGE",
                "first_frame": {"source": "page_003/clean_keyframe.png", "kind": "keyframe"},
                "last_frame": {"source": "page_003/clean_keyframe.png", "kind": "keyframe"},
            },
        ],
        "concat_plan": concat_plan if concat_plan is not None else [
            {"after_segment": "seg_001", "cut": "hard", "xfade_sec": 0, "pad_to_master_ratio": True},
        ],
    }


def test_plan_mode_layout():
    """两段 5s、第一段含两页 → 总 10s，页边界 0/2.5/5，前两页标 estimated。"""
    tl = bt.build_timeline(_plan(), None)
    assert tl["mode"] == "plan", tl["mode"]
    assert tl["page_count"] == 3, tl["page_count"]
    assert tl["segment_count"] == 2, tl["segment_count"]
    assert tl["total_sec"] == 10.0, tl["total_sec"]

    rows = tl["pages"]
    assert [r["page_id"] for r in rows] == ["page_001", "page_002", "page_003"]
    assert rows[0]["start_sec"] == 0.0 and rows[0]["end_sec"] == 2.5
    assert rows[1]["start_sec"] == 2.5 and rows[1]["end_sec"] == 5.0
    assert rows[2]["start_sec"] == 5.0 and rows[2]["end_sec"] == 10.0
    assert rows[0]["estimated"] is True and rows[1]["estimated"] is True
    assert rows[2]["estimated"] is False, "单页段不该标 estimated"
    assert tl["interaction_contract"]["estimated_pages"] == ["page_001", "page_002"]


def test_page_at_half_open_interval():
    """t=4.9 属于 page_002；t=5.0 属于 page_003（左闭右开）；超出总时长落到末页。"""
    tl = bt.build_timeline(_plan(), None)
    assert bt.page_at(tl, 0.0)["page_id"] == "page_001"
    assert bt.page_at(tl, 2.49)["page_id"] == "page_001"
    assert bt.page_at(tl, 2.5)["page_id"] == "page_002"
    assert bt.page_at(tl, 4.9)["page_id"] == "page_002"
    assert bt.page_at(tl, 5.0)["page_id"] == "page_003"
    assert bt.page_at(tl, 99.0)["page_id"] == "page_003"


def test_xfade_shifts_next_segment_and_warns():
    """第二段与前一段叠化 1s → 第二段起点 4.0，总时长 9.0，且有 warning。"""
    plan = _plan([{"after_segment": "seg_001", "cut": "xfade", "xfade_sec": 1.0,
                   "pad_to_master_ratio": True}])
    tl = bt.build_timeline(plan, None)
    assert tl["segments"][1]["start_sec"] == 4.0, tl["segments"][1]
    assert tl["total_sec"] == 9.0, tl["total_sec"]
    assert any("叠化" in w for w in tl["checks"]["warnings"]), tl["checks"]["warnings"]


def test_empty_segments_raises():
    try:
        bt.build_timeline({"segments": []}, None)
    except SystemExit as exc:
        assert "没有 segments" in str(exc)
        return
    raise AssertionError("空 segments 应当 SystemExit")


def test_cli_at_and_files():
    """CLI 走一遍：--at 打印页码；不带 --at 时落盘两个文件。"""
    d = _workdir()
    try:
        plan_path = d / "scene_plan.json"
        bt.write_text_lf(plan_path, json.dumps(_plan(), ensure_ascii=False))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = bt.main(["--plan", str(plan_path), "--at", "5.5"])
        assert code == 0, code
        assert "page_003" in buf.getvalue(), buf.getvalue()

        with contextlib.redirect_stdout(io.StringIO()):
            code = bt.main(["--plan", str(plan_path), "--out-dir", str(d / "out")])
        assert code == 0, code
        assert (d / "out" / "page_timeline.json").is_file()
        assert (d / "out" / "page_timeline.md").is_file()

        raw = (d / "out" / "page_timeline.json").read_bytes()
        assert b"\r\n" not in raw, "落盘必须 LF，否则 sha 与内存不一致"
        saved = json.loads(raw.decode("utf-8"))
        assert saved["page_count"] == 3
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[ok]   {name}")
        except Exception:
            failed += 1
            print(f"[FAIL] {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
