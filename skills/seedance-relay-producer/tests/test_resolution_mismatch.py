"""用例 4：输出视频宽高比/尺寸被平台篡改（如 4:3 被强行裁为 16:9），立即拒绝。"""
from pathlib import Path

import cv2

from scripts.common import load_pipeline_state, save_pipeline_state
from scripts.preflight import run_preflight
from scripts.qc import run_qc
from tests.conftest import populate_upstream_page, write_synthetic_video


def test_aspect_ratio_violation_rejected(tmp_path: Path, base_cfg: dict) -> None:
    pages_dir = tmp_path / "pages"
    out_dir = tmp_path / "out"

    kf1 = populate_upstream_page(pages_dir / "page_001", "page_001", width=640, height=480)
    run_preflight(pages_dir, out_dir, relay_arg="off", resolution="1080p", cfg=base_cfg)

    img1 = cv2.imread(str(kf1))
    vid1 = out_dir / "page_001" / "raw_clip.mp4"
    # 模拟输出被非法拉伸为 16:9 (854x480)
    write_synthetic_video(vid1, img1, img1, num_frames=120, fps=24, out_size=(854, 480))

    state = load_pipeline_state(out_dir)
    state["pages"]["page_001"]["status"] = "QC_CHECKING"
    state["pages"]["page_001"]["attempts"] = 1
    state["pages"]["page_001"]["current_job"] = {
        "attempt": 1,
        "task_id": "mock_ratio_bad",
        "duration_sec": 5,
        "resolution": "1080p",
        "raw_clip_path": str(vid1),
        "estimated_cost": 1.6,
    }
    save_pipeline_state(out_dir, state)

    qc_res = run_qc(out_dir, base_cfg)
    assert qc_res["page_001"]["verdict"] == "FAIL"
    assert any(d["code"] == "ASPECT_RATIO_VIOLATION" for d in qc_res["page_001"]["defects"])
