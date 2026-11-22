"""
离线测试夹具：生成合成绘本关键帧、模拟上游契约目录、生成假视频（含正常/接缝断裂/尺寸篡改/文字水印/时间漂移），
全程不消耗任何火山方舟 API 额度。
"""
import json
from pathlib import Path
from typing import Tuple

import cv2
import numpy as np
import pytest


def create_synthetic_keyframe(
    path: Path, width: int = 640, height: int = 480, bg_color: Tuple[int, int, int] = (230, 215, 195), shift_x: int = 0
) -> np.ndarray:
    """绘制一张带柔和渐变与几何角色的模拟绘本关键帧。"""
    img = np.full((height, width, 3), bg_color, dtype=np.uint8)
    # 地面色带
    img[int(height * 0.72) :, :] = (160, 195, 150)
    # 主体角色（固定位置的蓝色小鸟圆身 + 橙色喙）
    cx = int(width * 0.45) + shift_x
    cy = int(height * 0.55)
    cv2.circle(img, (cx, cy), 48, (185, 120, 75), -1)
    cv2.circle(img, (cx + 18, cy - 12), 6, (40, 35, 30), -1)
    pts = np.array([[cx + 46, cy - 5], [cx + 68, cy + 2], [cx + 46, cy + 10]], np.int32)
    cv2.fillPoly(img, [pts], (60, 150, 235))

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return img


def populate_upstream_page(
    page_dir: Path,
    page_id: str,
    width: int = 640,
    height: int = 480,
    qa_verdict: str = "PASS",
    shift_x: int = 0,
    scene_tag: str = "meadow_morning",
) -> Path:
    page_dir.mkdir(parents=True, exist_ok=True)
    kf_path = page_dir / "clean_keyframe.png"
    create_synthetic_keyframe(kf_path, width=width, height=height, shift_x=shift_x)

    manifest = {
        "manifest_version": "2.0.0",
        "pipeline_status": "COMPLETED_QA_PASSED",
        "page_id": page_id,
        "scene_tag": scene_tag,
        "character_ids": ["bluebird_01"],
        "asset_info": {
            "locked_resolution": {"width": width, "height": height},
            "locked_aspect_ratio": "4:3",
            "detected_language": "en",
        },
        "completion_items": [],
    }
    (page_dir / "page_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    qa_report = {
        "qa_verdict": qa_verdict,
        "resolution_delta_px": [0, 0],
        "ocr_residual_count": 0,
        "non_edited_ssim": 0.9999,
        "protected_zone_intrusion_px": 0,
    }
    (page_dir / "qa_report.json").write_text(json.dumps(qa_report, indent=2), encoding="utf-8")

    prompt_txt = (
        "[Camera]: Ultra-slow push-in.\n"
        "[Subject Motion]: The bluebird blinks gently.\n"
        "[Environment & Added Elements]: Soft breeze ripples the meadow grass.\n"
        "[Style Lock]: Preserve watercolor texture.\n"
        "[Negative Constraints]: NO text, NO subtitles, NO watermarks."
    )
    (page_dir / "seedance_prompt.txt").write_text(prompt_txt, encoding="utf-8")
    return kf_path


def write_synthetic_video(
    video_path: Path,
    start_bgr: np.ndarray,
    end_bgr: np.ndarray,
    num_frames: int = 120,
    fps: int = 24,
    out_size: Tuple[int, int] = None,
    inject_text: bool = False,
    inject_watermark: bool = False,
    inject_mid_drift: bool = False,
) -> None:
    """生成插值测试视频（mp4v 编码，OpenCV 与 ffprobe 均可直接解码）。"""
    video_path.parent.mkdir(parents=True, exist_ok=True)
    h, w = start_bgr.shape[:2]
    target_w, target_h = out_size if out_size else (w, h)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, float(fps), (target_w, target_h))

    for i in range(num_frames):
        alpha = i / float(max(num_frames - 1, 1))
        frame = cv2.addWeighted(start_bgr, 1.0 - alpha, end_bgr, alpha, 0.0)

        if inject_mid_drift and 0.2 <= alpha <= 0.8:
            # 中段注入大面积异色噪点块模拟画风/主体崩坏
            frame[40 : h - 40, 40 : w - 40] = (20, 20, 220)

        if inject_text:
            cv2.putText(frame, "SUBTITLE TEST 123", (120, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (10, 10, 10), 3)

        if inject_watermark:
            # 在右下角注入高对比度仿平台角标
            cv2.rectangle(frame, (w - 72, h - 34), (w - 6, h - 6), (255, 255, 255), -1)
            cv2.line(frame, (w - 70, h - 32), (w - 8, h - 8), (0, 0, 0), 2)
            cv2.line(frame, (w - 70, h - 8), (w - 8, h - 32), (0, 0, 0), 2)

        if (target_w, target_h) != (w, h):
            frame = cv2.resize(frame, (target_w, target_h))
        writer.write(frame)
    writer.release()


@pytest.fixture
def base_cfg() -> dict:
    return {
        "ark": {
            "base_url": "https://ark.mock.local/api/v3",
            "model": "doubao-seedance-1-0-pro-250528",
            "supports_last_frame": True,
            "allowed_durations": [5, 10],
            "allowed_ratios": ["16:9", "4:3", "1:1"],
            "max_prompt_length": 1000,
            "request_timeout_sec": 10,
            "poll_initial_interval_sec": 0.1,
            "poll_max_interval_sec": 0.5,
            "poll_total_timeout_sec": 5,
        },
        "pricing": {
            "currency": "RMB",
            "budget_guard_limit": 20.0,
            "unit_cost": {"480p_5s": 0.4, "1080p_5s": 1.6, "1080p_10s": 3.2},
        },
        "relay": {
            "default_mode": "auto",
            "dino_threshold_5s": 0.20,
            "dino_threshold_10s": 0.35,
            "dino_threshold_level_b": 0.55,
            "level_b_transition": "fade",
            "level_b_transition_sec": 0.4,
        },
        "thresholds": {
            "upstream_min_ssim": 0.9990,
            "upstream_max_intrusion_px": 0,
            "first_frame_min_ssim": 0.92,
            "first_frame_max_phash_dist": 6,
            "last_frame_min_ssim": 0.88,
            "seam_min_ssim": 0.88,
            "temporal_min_dino_cos": 0.78,
            "temporal_max_hist_bhattacharyya": 0.28,
            "cross_page_max_brightness_zscore": 2.5,
            "ocr_max_allowed_chars": 0,
            "watermark_corner_edge_density_max": 0.18,
        },
        "repair": {"max_retries_per_page": 3, "base_seed": 1000, "seed_step": 100},
        "runtime": {"prefer_gpu": False, "target_fps": 24, "audio_sample_rate": 48000},
    }
