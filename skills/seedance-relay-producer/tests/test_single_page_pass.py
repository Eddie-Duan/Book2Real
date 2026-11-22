"""用例 1：正常单页独立生成与自检通过（auto 模式下单页自动关闭接力）。"""
from pathlib import Path

import cv2

from scripts.common import load_pipeline_state, save_pipeline_state
from scripts.preflight import run_preflight
from scripts.qc import run_qc
from scripts.report import generate_reports
from tests.conftest import populate_upstream_page, write_synthetic_video


def test_single_page_pass(tmp_path: Path, base_cfg: dict) -> None:
    pages_dir = tmp_path / "pages"
    out_dir = tmp_path / "out"

    kf1 = populate_upstream_page(pages_dir / "page_001", "page_001")
    pf = run_preflight(pages_dir, out_dir, relay_arg="auto", resolution="1080p", cfg=base_cfg)

    assert pf["ready_count"] == 1
    assert pf["relay_enabled"] is False

    # 桩生成：首尾保持一致的平滑呼吸动画
    img1 = cv2.imread(str(kf1))
    raw_vid = out_dir / "page_001" / "raw_clip.mp4"
    write_synthetic_video(raw_vid, img1, img1, num_frames=120, fps=24)

    state = load_pipeline_state(out_dir)
    state["pages"]["page_001"]["status"] = "QC_CHECKING"
    state["pages"]["page_001"]["attempts"] = 1
    state["pages"]["page_001"]["current_job"] = {
        "attempt": 1,
        "task_id": "mock_task_001",
        "duration_sec": 5,
        "resolution": "1080p",
        "raw_clip_path": str(raw_vid),
        "estimated_cost": 1.6,
    }
    state["pages"]["page_001"]["history"].append(state["pages"]["page_001"]["current_job"])
    save_pipeline_state(out_dir, state)

    qc_res = run_qc(out_dir, base_cfg)
    assert qc_res["page_001"]["verdict"] == "PASS"
    assert (out_dir / "page_001" / "clip.mp4").is_file()

    rep = generate_reports(out_dir)
    assert rep["summary_counts"]["PASS"] == 1
