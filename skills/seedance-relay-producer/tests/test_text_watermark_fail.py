"""用例 5：生成画面中检出字幕文字或平台角落水印，触发缺陷拦截与返修。"""
from pathlib import Path

import cv2

from scripts.common import load_pipeline_state, save_pipeline_state
from scripts.preflight import run_preflight
from scripts.qc import run_qc
from tests.conftest import populate_upstream_page, write_synthetic_video


def test_text_and_watermark_detected(tmp_path: Path, base_cfg: dict) -> None:
    pages_dir = tmp_path / "pages"
    out_dir = tmp_path / "out"

    kf1 = populate_upstream_page(pages_dir / "page_001", "page_001")
    run_preflight(pages_dir, out_dir, relay_arg="off", resolution="1080p", cfg=base_cfg)

    img1 = cv2.imread(str(kf1))
    vid1 = out_dir / "page_001" / "raw_clip.mp4"
    write_synthetic_video(vid1, img1, img1, num_frames=120, fps=24, inject_text=True, inject_watermark=True)

    state = load_pipeline_state(out_dir)
    state["pages"]["page_001"]["status"] = "QC_CHECKING"
    state["pages"]["page_001"]["attempts"] = 1
    state["pages"]["page_001"]["current_job"] = {
        "attempt": 1,
        "task_id": "mock_text_wm",
        "duration_sec": 5,
        "resolution": "1080p",
        "raw_clip_path": str(vid1),
        "estimated_cost": 1.6,
    }
    save_pipeline_state(out_dir, state)

    qc_res = run_qc(out_dir, base_cfg)
    codes = {d["code"] for d in qc_res["page_001"]["defects"]}
    assert qc_res["page_001"]["verdict"] == "FAIL"
    assert "TEXT_DETECTED" in codes
    assert "WATERMARK_DETECTED" in codes
