"""用例 2：正常双页首尾帧双锚定接力（K_1 -> K_2 无缝闭合，末页 K_2 自然收束）。"""
from pathlib import Path

import cv2

from scripts.common import load_pipeline_state, save_pipeline_state
from scripts.preflight import run_preflight
from scripts.qc import run_qc
from tests.conftest import populate_upstream_page, write_synthetic_video


def test_relay_dual_anchor_pass(tmp_path: Path, base_cfg: dict) -> None:
    pages_dir = tmp_path / "pages"
    out_dir = tmp_path / "out"

    kf1 = populate_upstream_page(pages_dir / "page_001", "page_001", shift_x=0)
    kf2 = populate_upstream_page(pages_dir / "page_002", "page_002", shift_x=15)

    pf = run_preflight(pages_dir, out_dir, relay_arg="auto", resolution="1080p", cfg=base_cfg)
    assert pf["relay_enabled"] is True
    assert len(pf["relay_edges"]) == 1
    assert pf["relay_edges"][0]["level"] == "A"

    img1 = cv2.imread(str(kf1))
    img2 = cv2.imread(str(kf2))

    # 桩生成：page_001 从 K_1 走到 K_2；page_002 从 K_2 收束
    vid1 = out_dir / "page_001" / "raw_clip.mp4"
    vid2 = out_dir / "page_002" / "raw_clip.mp4"
    write_synthetic_video(vid1, img1, img2, num_frames=120, fps=24)
    write_synthetic_video(vid2, img2, img2, num_frames=120, fps=24)

    state = load_pipeline_state(out_dir)
    for idx, (pid, vpath) in enumerate([("page_001", vid1), ("page_002", vid2)], start=1):
        state["pages"][pid]["status"] = "QC_CHECKING"
        state["pages"][pid]["attempts"] = 1
        state["pages"][pid]["current_job"] = {
            "attempt": 1,
            "task_id": f"mock_relay_{idx}",
            "duration_sec": 5,
            "resolution": "1080p",
            "raw_clip_path": str(vpath),
            "estimated_cost": 1.6,
        }
    save_pipeline_state(out_dir, state)

    qc_res = run_qc(out_dir, base_cfg)
    assert qc_res["page_001"]["verdict"] == "PASS"
    assert qc_res["page_001"]["checks"]["seam"]["passed"] is True
    assert qc_res["page_002"]["verdict"] == "PASS"
