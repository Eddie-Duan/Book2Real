#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_pipeline.py 的最小回归（纯标准库，不依赖 cv2 / pytest）。

跑法：
    python tests/test_run_pipeline.py

它只验"编排层"最容易坏的部分，不触网、不花钱、不生成视频：
1. 八步顺序与花钱标记（--list）；
2. 页目录不存在 / 缺 clean_keyframe 时报错清楚，而不是跑一半炸；
3. --from 在 --to 之后直接拒绝；
4. 定位 scene-director 的两种方式（显式给 / 找不到时给提示）。
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_pipeline as rp  # noqa: E402

EXPECTED_STEPS = [
    ("plan", False),
    ("duration", False),
    ("bridge", False),
    ("preflight", False),
    ("generate", True),
    ("poll", True),
    ("qc", False),
    ("concat", False),
    ("timeline", False),
]


def _base_dir() -> Path:
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


def test_step_order_and_cost_flags():
    """--list 输出的顺序必须与 STEPS 一致，且只有 generate/poll 标"花钱"。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = rp.main(["--list"])
    assert code == 0, code
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == len(EXPECTED_STEPS), lines

    keys = [s.key for s in rp.STEPS]
    assert keys == [k for k, _ in EXPECTED_STEPS], keys
    costly = [s.key for s in rp.STEPS if s.costly]
    assert costly == ["generate", "poll"], costly

    for ln, (key, is_costly) in zip(lines, EXPECTED_STEPS):
        assert key in ln, (key, ln)
        assert ("花钱" in ln) is is_costly, (key, ln)


def test_missing_pages_dir_stops_early():
    d = _workdir()
    try:
        try:
            rp.main(["--pages-dir", str(d / "nope"), "--dry-run"])
        except SystemExit as exc:
            assert "页目录不存在" in str(exc), exc
            return
        raise AssertionError("不存在的页目录应当 SystemExit")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_pages_dir_without_keyframes_stops_early():
    d = _workdir()
    try:
        (d / "pages" / "page_001").mkdir(parents=True)  # 空目录，没有 clean_keyframe.png
        try:
            rp.main(["--pages-dir", str(d / "pages"), "--dry-run"])
        except SystemExit as exc:
            assert "clean_keyframe.png" in str(exc), exc
            return
        raise AssertionError("空页目录应当 SystemExit")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_from_after_to_rejected():
    d = _workdir()
    try:
        page = d / "pages" / "page_001"
        page.mkdir(parents=True)
        (page / "clean_keyframe.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # 只要存在即可

        # 显式给两个上游根目录，避免测试依赖仓库当前布局
        scene = d / "scene" / "scripts"
        scene.mkdir(parents=True)
        (scene / "plan_scenes.py").write_text("# stub\n", encoding="utf-8")
        relay = d / "relay"
        (relay / "scripts").mkdir(parents=True)
        (relay / "scripts" / "preflight.py").write_text("# stub\n", encoding="utf-8")

        try:
            rp.main(["--pages-dir", str(d / "pages"), "--dry-run",
                     "--scene-scripts", str(scene), "--relay-root", str(relay),
                     "--from", "qc", "--to", "preflight"])
        except SystemExit as exc:
            assert "--from 在 --to 之后" in str(exc), exc
            return
        raise AssertionError("--from 在 --to 之后应当 SystemExit")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_find_scene_scripts_explicit(tmp: Path | None = None):
    """显式给的目录里有 plan_scenes.py 才接受；没有则报清楚的错。"""
    d = _workdir()
    try:
        scripts = d / "scene" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "plan_scenes.py").write_text("# stub\n", encoding="utf-8")

        got = rp.find_scene_scripts(scripts)
        assert got == scripts.resolve(), got

        empty = d / "empty"
        empty.mkdir()
        try:
            rp.find_scene_scripts(empty)
        except SystemExit as exc:
            assert "没有 plan_scenes.py" in str(exc), exc
            return
        raise AssertionError("缺 plan_scenes.py 应当 SystemExit")
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
