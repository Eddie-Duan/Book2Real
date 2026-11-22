"""用例 3：双锚定接缝不达标（尾帧未走到 K_{N+1}），触发单页返修并将时长升至 10s。"""
from pathlib import Path

import cv2

from scripts.common import load_pipeline_state, save_pipeline_state
from scripts.preflight import run_preflight
from scripts.qc import run_qc
from tests.conftest import create_synthetic_keyframe, populate_upstream_page, write_synthetic_video


def test_seam_mismatch_triggers_single_page_repair(tmp_path: Path, base_cfg: dict) -> None:
    pages_dir = tmp_path / "pages"
    out_dir = tmp_path / "out"

    kf1 = populate_upstream_page(pages_dir / "page_001", "page_001", shift_x=0)
    populate_upstream_page(pages_dir / "page_002", "page_002", shift_x=25)

    run_preflight(pages_dir, out_dir, relay_arg="auto", resolution="1080p", cfg=base_cfg)

    img1 = cv2.imread(str(kf1))
    # 模拟失败：尾帧停留在完全不同的偏移位置，未抵达 K_2
    wrong_end = create_synthetic_keyframe(tmp_path / "wrong_end.png", shift_x=-140)
    vid1 = out_dir / "page_001" / "raw_clip.mp4"
    write_synthetic_video(vid1, img1, wrong_end, num_frames=120, fps=24)

    state = load_pipeline_state(out_dir)
    state["pages"]["page_001"]["status"] = "QC_CHECKING"
    state["pages"]["page_001"]["attempts"] = 1
    state["pages"]["page_001"]["current_job"] = {
        "attempt": 1,
        "task_id": "mock_seam_fail",
        "duration_sec": 5,
        "resolution": "1080p",
        "raw_clip_path": str(vid1),
        "estimated_cost": 1.6,
    }
    save_pipeline_state(out_dir, state)

    qc_res = run_qc(out_dir, base_cfg, target_page_id="page_001")
    assert qc_res["page_001"]["verdict"] == "FAIL"
    assert any(d["code"] == "SEAM_MISMATCH" for d in qc_res["page_001"]["defects"])

    updated = load_pipeline_state(out_dir)
    assert updated["pages"]["page_001"]["status"] == "REPAIRING"
    assert updated["pages"]["page_001"]["preflight"]["generation_plan"]["duration_sec"] == 10
    # 验证下游 page_002 未受级联污染
    assert updated["pages"]["page_002"]["status"] == "PREFLIGHT_READY"
