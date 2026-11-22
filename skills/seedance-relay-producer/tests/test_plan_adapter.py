#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_plan_adapter.py — plan_adapter 的纯脚本回归（不需要 pytest，不写系统 TEMP）。

跑法（在 seedance-relay-producer 目录下，用带 cv2 的解释器）：
    .venv\\Scripts\\python.exe tests\\test_plan_adapter.py

为什么不用 pytest：本仓库的 pytest 用例会在 %TEMP% 下建 0o700 目录，在部分受限环境
（如带文件沙箱的 agent 会话）里会被拒写。这里的临时目录一律建在仓库内的 _tmp_tests/。
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from inputs.plan_adapter import PlanContractError, build_plan_records  # noqa: E402

_BaseDir = None


def _base_dir() -> Path:
    """挑一个真正能写目录的临时根（仓库内优先，其次系统临时目录）。"""
    global _BaseDir
    if _BaseDir is not None:
        return _BaseDir
    import tempfile

    for candidate in (ROOT / "_tmp_tests", Path(tempfile.gettempdir()) / "seedance_plan_tests"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / f".probe{uuid.uuid4().hex[:6]}"
            probe.mkdir()
            probe.rmdir()
            _BaseDir = candidate
            return candidate
        except OSError:
            continue
    raise RuntimeError("找不到可写的临时目录：仓库内 _tmp_tests 与系统 TEMP 都不可写")

CFG = {
    "thresholds": {"upstream_min_ssim": 0.9990, "upstream_max_intrusion_px": 0},
    "ark": {
        "max_prompt_length": 1000,
        "allowed_ratios": ["4:3", "16:9", "9:16", "1:1"],
    },
}

PROMPT = (
    "[Camera] Locked-off static camera, slow push in.\n"
    "[Subject Motion] The bear cub walks forward and stops.\n"
    "[Environment Motion] Leaves drift slowly.\n"
    "[Negative Constraints] no text, no watermark, no extra characters.\n"
)


def _workdir() -> Path:
    base = _base_dir()
    d = base / f"plan{uuid.uuid4().hex[:10]}"
    d.mkdir(parents=True)
    return d


def _png(path: Path, size=(320, 240), color=(120, 150, 110)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def _make_pages(root: Path, page_ids) -> Path:
    pages = root / "pages"
    for i, pid in enumerate(page_ids):
        pdir = pages / pid
        pdir.mkdir(parents=True, exist_ok=True)
        _png(pdir / "clean_keyframe.png", color=(80 + i * 10, 120, 100))
        (pdir / "page_manifest.json").write_text(
            json.dumps(
                {
                    "page_id": pid,
                    "scene_tag": f"sc_{i}",
                    "character_ids": ["bear"],
                    "time_tag": "afternoon",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (pdir / "qa_report.json").write_text(
            json.dumps(
                {
                    "verdict": "PASS",
                    "ocr_residual_count": 0,
                    "non_edited_ssim": 1.0,
                    "protected_zone_intrusion_px": 0,
                }
            ),
            encoding="utf-8",
        )
        (pdir / "seedance_prompt.txt").write_text(PROMPT, encoding="utf-8")
    return pages


def _make_plan(root: Path, segments, concat=None, seam_realization=None) -> Path:
    d = root / "director_out"
    (d / "prompts").mkdir(parents=True, exist_ok=True)
    (d / "bridge").mkdir(parents=True, exist_ok=True)
    for bridge in ("T_shared.png", "T_shared2.png"):
        _png(d / "bridge" / bridge, color=(20, 20, 20))
    for seg in segments:
        (d / "prompts" / f"{seg['segment_id']}_prompt.txt").write_text(PROMPT, encoding="utf-8")
    plan = {
        "plan_version": "1.0.0",
        "master_ratio": "4:3",
        "pages": [],
        "segments": segments,
        "seams": [],
        "concat_plan": concat or [],
    }
    (d / "scene_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return d / "scene_plan.json"


def _seg(seg_id, pages, first, last, dur=5, kind="SINGLE_PAGE", realization="NONE"):
    return {
        "segment_id": seg_id,
        "pages": pages,
        "segment_kind": kind,
        "duration_sec": dur,
        "first_frame": first,
        "last_frame": last,
        "prompt_file": f"prompts/{seg_id}_prompt.txt",
        "transition_out": {"type": "HARD_CUT", "subtype": "CUT_ON_ACTION", "realization": realization},
    }


def _kf(pid):
    return {"source": f"{pid}/clean_keyframe.png", "kind": "keyframe"}


def _bf(name, kind="black"):
    return {"source": f"bridge/{name}", "kind": "bridge_frame", "bridge_kind": kind}


def test_two_segments_hidden_cut():
    root = _workdir()
    try:
        pages = _make_pages(root, ["page_001", "page_002"])
        plan = _make_plan(
            root,
            [
                _seg("seg_001", ["page_001"], _kf("page_001"), _kf("page_002")),
                _seg("seg_002", ["page_002"], _kf("page_002"), None, realization="NONE"),
            ],
            concat=[{"after_segment": "seg_001", "cut": "hard", "xfade_sec": 0.0}],
        )
        built = build_plan_records(plan, pages, CFG)
        recs = built["records"]
        assert len(recs) == 2, recs
        assert all(r["status"] == "PREFLIGHT_READY" for r in recs), [r["block_reasons"] for r in recs]
        g1, g2 = recs[0]["generation_plan"], recs[1]["generation_plan"]
        # 隐藏切：尾帧就是本段自己的 last_frame，不是下一段首帧
        assert Path(g1["last_frame_path"]).name == "clean_keyframe.png"
        assert "page_002" in g1["last_frame_path"]
        assert g1["relay_level"] == "A"
        assert g2["last_frame_path"] is None and g2["relay_level"] == "INDEPENDENT"
        assert g2["is_terminal_page"] is True and g2["needs_ending_lock"] is True
        assert g1["transition_after"] is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_shared_t_consistent():
    root = _workdir()
    try:
        pages = _make_pages(root, ["page_001", "page_002"])
        shared = _bf("T_shared.png")
        plan = _make_plan(
            root,
            [
                _seg("seg_001", ["page_001"], _kf("page_001"), shared, realization="SHARED_T"),
                _seg("seg_002", ["page_002"], shared, None, realization="NONE"),
            ],
            concat=[{"after_segment": "seg_001", "cut": "xfade", "xfade_sec": 1.5}],
        )
        recs = build_plan_records(plan, pages, CFG)["records"]
        assert all(r["status"] == "PREFLIGHT_READY" for r in recs), [r["block_reasons"] for r in recs]
        g1 = recs[0]["generation_plan"]
        assert g1["last_frame_path"].endswith("T_shared.png")
        assert g1["transition_after"] == "fade" and abs(g1["transition_sec"] - 1.5) < 1e-6
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_shared_t_mismatch_blocks():
    root = _workdir()
    try:
        pages = _make_pages(root, ["page_001", "page_002"])
        plan = _make_plan(
            root,
            [
                _seg("seg_001", ["page_001"], _kf("page_001"), _bf("T_shared.png"), realization="SHARED_T"),
                _seg("seg_002", ["page_002"], _bf("T_shared2.png"), None, realization="NONE"),
            ],
        )
        recs = build_plan_records(plan, pages, CFG)["records"]
        assert recs[0]["status"] == "BLOCKED"
        assert any("SHARED_T" in r for r in recs[0]["block_reasons"]), recs[0]["block_reasons"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_missing_anchor_blocks():
    root = _workdir()
    try:
        pages = _make_pages(root, ["page_001"])
        plan = _make_plan(
            root,
            [_seg("seg_001", ["page_001"], _kf("page_001"), _bf("T_missing.png"))],
        )
        recs = build_plan_records(plan, pages, CFG)["records"]
        assert recs[0]["status"] == "BLOCKED"
        assert any("不存在" in r for r in recs[0]["block_reasons"]), recs[0]["block_reasons"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_duration_blocks():
    root = _workdir()
    try:
        pages = _make_pages(root, ["page_001"])
        plan = _make_plan(
            root,
            [_seg("seg_001", ["page_001"], _kf("page_001"), None, dur=8)],
        )
        recs = build_plan_records(plan, pages, CFG)["records"]
        assert recs[0]["status"] == "BLOCKED"
        assert any("段时长" in r for r in recs[0]["block_reasons"]), recs[0]["block_reasons"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_upstream_qa_fail_blocks():
    root = _workdir()
    try:
        pages = _make_pages(root, ["page_001"])
        (pages / "page_001" / "qa_report.json").write_text(
            json.dumps({"verdict": "FAIL", "ocr_residual_count": 0, "non_edited_ssim": 1.0,
                        "protected_zone_intrusion_px": 0}),
            encoding="utf-8",
        )
        plan = _make_plan(root, [_seg("seg_001", ["page_001"], _kf("page_001"), None)])
        recs = build_plan_records(plan, pages, CFG)["records"]
        assert recs[0]["status"] == "BLOCKED"
        assert any("qa 判定未通过" in r for r in recs[0]["block_reasons"]), recs[0]["block_reasons"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty_plan_raises():
    root = _workdir()
    try:
        pages = _make_pages(root, ["page_001"])
        plan = _make_plan(root, [])
        try:
            build_plan_records(plan, pages, CFG)
        except PlanContractError as exc:
            assert any("segments" in p for p in exc.problems), exc.problems
        else:
            raise AssertionError("空计划应当抛 PlanContractError")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[ERROR] {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"[ok] {name}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
