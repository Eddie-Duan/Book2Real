#!/usr/bin/env python3
"""
Step 4 & Step 5: 多维度量化自检门禁 (ffprobe + 首尾帧SSIM + DINOv2漂移 + 无中生有 + OCR文字 + 水印)
及自动返修闭环 / 人工升级导出。
"""
import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import imagehash
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.common import (
    TextDetector,
    VisualFeatureExtractor,
    append_run_log,
    compute_hsv_bhattacharyya,
    compute_ssim_pair,
    extract_video_frames,
    imread_bgr,
    imwrite_bgr,
    load_config,
    load_pipeline_state,
    run_ffprobe_info,
    save_pipeline_state,
)


def compute_phash_distance(img_a: np.ndarray, img_b: np.ndarray) -> int:
    pil_a = Image.fromarray(cv2.cvtColor(img_a, cv2.COLOR_BGR2RGB))
    pil_b = Image.fromarray(cv2.cvtColor(img_b, cv2.COLOR_BGR2RGB))
    return int(imagehash.phash(pil_a) - imagehash.phash(pil_b))


def compute_zoom_ratio(ref_bgr: np.ndarray, other_bgr: np.ndarray) -> Optional[float]:
    """用 ORB 特征 + 局部仿射估计 other 相对 ref 的整体尺度比（>1 = 被放大/推近）。

    DINO/SSIM 对整体缩放几乎不敏感（缩放后的画面仍会被判高度一致），
    所以"每页都从远到近推近"必须单独用几何尺度量。特征不够（纯色 T、小图）返回 None。
    """
    ref_gray = cv2.cvtColor(ref_bgr, cv2.COLOR_BGR2GRAY)
    other_gray = cv2.cvtColor(other_bgr, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=1500)
    kp_ref, des_ref = orb.detectAndCompute(ref_gray, None)
    kp_other, des_other = orb.detectAndCompute(other_gray, None)
    if des_ref is None or des_other is None or len(kp_ref) < 12 or len(kp_other) < 12:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    knn = matcher.knnMatch(des_ref, des_other, k=2)
    good = [m for m, n in (pair for pair in knn if len(pair) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 15:
        return None
    # 只用画面外圈的特征点估尺度：角色/主体在中间动来动去会把局部位移误判成缩放，
    # 背景外圈只有真正的推近/拉远才会统一缩放。
    h, w = ref_gray.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    ring = [
        m
        for m in good
        if abs(kp_ref[m.queryIdx].pt[0] - cx) > 0.30 * w
        or abs(kp_ref[m.queryIdx].pt[1] - cy) > 0.30 * h
    ]
    if len(ring) < 15:
        ring = good
    src = np.float32([kp_ref[m.queryIdx].pt for m in ring]).reshape(-1, 1, 2)
    dst = np.float32([kp_other[m.trainIdx].pt for m in ring]).reshape(-1, 1, 2)
    matrix, inliers = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0
    )
    if matrix is None or inliers is None or int(inliers.sum()) < 10:
        return None
    scale = float(np.sqrt(matrix[0, 0] ** 2 + matrix[1, 0] ** 2))
    if not np.isfinite(scale) or scale <= 0:
        return None
    return scale


def to_gray_std(img_bgr: np.ndarray) -> float:
    """灰度标准差，用来判断"这张参照帧是不是近乎纯色"。"""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return float(gray.std())


def detect_corner_watermark(
    frame_bgr: np.ndarray,
    ref_bgr: np.ndarray,
    edge_threshold: float,
    changed_frac_min: float = 0.35,
    changed_edge_density_min: float = 0.10,
) -> Dict[str, Any]:
    """检测四角（12% x 8%）是否出现原关键帧不存在的新增高频角标/水印。

    单一"新增边缘像素占 ROI 比例"判据会漏掉贴片式角标：白底 + 几条笔画铺满角落时，
    边缘只占 ROI 的一小圈（实测 0.144 < 0.18 阈值），但整个角落面积都被改写。
    因此加第二条并集判据：变更面积占比够大 且 新增边缘密度不低 -> 视为覆盖型角标。
    两条阈值都可由 config.thresholds 覆盖，环境动效大的项目可自行调高。
    """
    if frame_bgr.shape[:2] != ref_bgr.shape[:2]:
        ref_resized = cv2.resize(ref_bgr, (frame_bgr.shape[1], frame_bgr.shape[0]), interpolation=cv2.INTER_AREA)
    else:
        ref_resized = ref_bgr

    h, w = frame_bgr.shape[:2]
    ch, cw = max(int(h * 0.08), 16), max(int(w * 0.12), 24)
    corners = {
        "top_left": (0, ch, 0, cw),
        "top_right": (0, ch, w - cw, w),
        "bottom_left": (h - ch, h, 0, cw),
        "bottom_right": (h - ch, h, w - cw, w),
    }

    flagged_corners = []
    max_new_edge_density = 0.0
    max_changed_frac = 0.0

    for cname, (y1, y2, x1, x2) in corners.items():
        roi_f = cv2.cvtColor(frame_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        roi_r = cv2.cvtColor(ref_resized[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        edges_f = cv2.Canny(roi_f, 80, 180) > 0
        edges_r = cv2.Canny(roi_r, 80, 180) > 0
        new_edges = np.logical_and(edges_f, np.logical_not(edges_r))
        density = float(np.mean(new_edges))
        changed_frac = float(np.mean(cv2.absdiff(roi_f, roi_r) > 20))
        if density > max_new_edge_density:
            max_new_edge_density = density
        if changed_frac > max_changed_frac:
            max_changed_frac = changed_frac
        covered = changed_frac >= changed_frac_min and density >= changed_edge_density_min
        if density > edge_threshold or covered:
            flagged_corners.append(
                {
                    "corner": cname,
                    "new_edge_density": round(density, 4),
                    "changed_frac": round(changed_frac, 4),
                    "reason": "edge_density" if density > edge_threshold else "covered_area",
                }
            )

    return {
        "passed": len(flagged_corners) == 0,
        "max_new_edge_density": round(max_new_edge_density, 4),
        "max_changed_frac": round(max_changed_frac, 4),
        "threshold": edge_threshold,
        "changed_frac_threshold": changed_frac_min,
        "flagged_corners": flagged_corners,
    }


def detect_hallucinated_objects(
    frame_bgr: np.ndarray, ref_bgr: np.ndarray, whitelist_terms: List[str]
) -> Dict[str, Any]:
    """检测视频帧中是否出现原关键帧不存在的大面积独立新增前景物体。"""
    if frame_bgr.shape[:2] != ref_bgr.shape[:2]:
        ref_resized = cv2.resize(ref_bgr, (frame_bgr.shape[1], frame_bgr.shape[0]), interpolation=cv2.INTER_AREA)
    else:
        ref_resized = ref_bgr

    diff = cv2.absdiff(cv2.GaussianBlur(frame_bgr, (5, 5), 0), cv2.GaussianBlur(ref_resized, (5, 5), 0))
    gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray_diff, 42, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    total_area = float(frame_bgr.shape[0] * frame_bgr.shape[1])
    max_blob_ratio = 0.0
    for lbl in range(1, num_labels):
        area = float(stats[lbl, cv2.CC_STAT_AREA])
        ratio = area / total_area
        if ratio > max_blob_ratio:
            max_blob_ratio = ratio

    # 若环境白名单包含显著动态现象（如暴雨、大浪、雪崩），适当放宽连通域面积容忍度
    heavy_motion_words = {"rain", "snow", "wave", "waves", "smoke", "clouds", "雨", "雪", "浪", "烟", "云"}
    has_heavy_env = bool(set(whitelist_terms) & heavy_motion_words)
    limit = 0.065 if has_heavy_env else 0.035

    return {
        "passed": max_blob_ratio <= limit,
        "max_new_blob_area_ratio": round(max_blob_ratio, 4),
        "threshold": limit,
        "allowed_environment_whitelist": whitelist_terms[:20],
    }


def build_contact_sheet(
    keyframe_bgr: np.ndarray,
    sampled_frames: List[tuple],
    dest_path: Path,
    last_target_bgr: Optional[np.ndarray] = None,
) -> None:
    """生成包含首帧真值、采样帧及目标尾帧的横向拼图，供质检与人工审阅。"""
    thumb_h = 270
    scale = thumb_h / float(keyframe_bgr.shape[0])
    thumb_w = int(round(keyframe_bgr.shape[1] * scale))

    tiles = []
    ref_tile = cv2.resize(keyframe_bgr, (thumb_w, thumb_h))
    cv2.putText(ref_tile, "KEYFRAME_N (GT)", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 0), 2)
    tiles.append(ref_tile)

    for frac, fidx, fimg in sampled_frames:
        t = cv2.resize(fimg, (thumb_w, thumb_h))
        cv2.putText(t, f"{int(frac*100)}% (f={fidx})", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        tiles.append(t)

    if last_target_bgr is not None:
        end_tile = cv2.resize(last_target_bgr, (thumb_w, thumb_h))
        cv2.putText(end_tile, "KEYFRAME_N+1 (GT)", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
        tiles.append(end_tile)

    sheet = np.hstack(tiles)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    imwrite_bgr(dest_path, sheet)


def inspect_single_page(
    pstate: Dict[str, Any],
    out_dir: Path,
    cfg: Dict[str, Any],
    extractor: VisualFeatureExtractor,
    text_detector: TextDetector,
) -> Dict[str, Any]:
    pid = pstate["page_id"]
    pf = pstate["preflight"]
    plan = pf["generation_plan"]
    job = pstate["current_job"]
    thresh = cfg["thresholds"]

    page_dir = out_dir / pid
    video_path = Path(job["raw_clip_path"])
    kf_path = Path(pf["keyframe_path"])

    kf_bgr = imread_bgr(kf_path)
    if kf_bgr is None:
        raise RuntimeError(f"Cannot read keyframe {kf_path}")

    # 段的首帧可能是导演合成的桥接帧 T（黑场/模糊/遮挡）。T 不是"内容画面"，
    # 拿它当逐帧参照做时间漂移、无中生有、角落水印必然误报；近纯色帧的 pHash 也不可靠
    # （整幅黑场的 pHash 会和另一张黑场差 32 位）。这三种情况都要按"参照物不适用"处理。
    seg_meta = pf.get("segment") or {}
    ff_source = str(seg_meta.get("first_frame_source") or "").replace("\\", "/")
    lf_source = str(seg_meta.get("last_frame_source") or plan.get("last_frame_source") or "").replace("\\", "/")
    first_kind = (seg_meta.get("first_frame") or {}).get("kind")
    last_kind = (seg_meta.get("last_frame") or {}).get("kind")
    anchor_is_bridge = (
        first_kind == "bridge_frame"
        or "/bridge/" in str(kf_path).replace("\\", "/")
        or ff_source.startswith("bridge/")
    )
    anchor_is_flat = float(to_gray_std(kf_bgr)) < 8.0
    anchor_unusable = anchor_is_bridge or anchor_is_flat
    # 段尾扎进桥接帧 T（黑场/遮挡）时，后半段本来就该"变暗/被遮住"，
    # 拿首帧当参照只会把计划内的转场判成漂移；这类段只在前半段做一致性判定。
    tail_is_bridge = last_kind == "bridge_frame" or lf_source.startswith("bridge/")

    defects: List[Dict[str, Any]] = []

    # 1. ffprobe 结构检查
    probe = run_ffprobe_info(video_path)
    v_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    a_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    if not v_streams:
        defects.append({"code": "CORRUPT_STREAM", "detail": "视频文件不包含有效 video stream", "frame": -1})
        return {"page_id": pid, "verdict": "FAIL", "defects": defects}

    vs = v_streams[0]
    out_w, out_h = int(vs["width"]), int(vs["height"])
    in_w, in_h = pf["width"], pf["height"]
    ar_err = abs((out_w / float(out_h)) - (in_w / float(in_h)))
    actual_dur = float(probe.get("format", {}).get("duration") or vs.get("duration") or 0.0)
    target_dur = float(job["duration_sec"])

    structure_check = {
        "input_resolution": f"{in_w}x{in_h}",
        "output_resolution": f"{out_w}x{out_h}",
        "aspect_ratio_error": round(ar_err, 5),
        "actual_duration_sec": round(actual_dur, 3),
        "target_duration_sec": target_dur,
        "has_audio_stream": len(a_streams) > 0,
        "passed": ar_err <= 0.005 and abs(actual_dur - target_dur) <= 0.45,
    }
    if ar_err > 0.005:
        defects.append(
            {
                "code": "ASPECT_RATIO_VIOLATION",
                "detail": f"输出宽高比偏差 {ar_err:.4f} > 0.005 ({out_w}x{out_h} vs {in_w}x{in_h})",
                "frame": 0,
            }
        )
    if abs(actual_dur - target_dur) > 0.45:
        defects.append(
            {
                "code": "DURATION_MISMATCH",
                "detail": f"实际时长 {actual_dur:.2f}s 与目标 {target_dur}s 偏差过大",
                "frame": -1,
            }
        )

    # 2. 抽帧: 0%, 25%, 50%, 75%, 100%
    sampled = extract_video_frames(video_path, [0.0, 0.25, 0.50, 0.75, 1.0])
    first_frame = sampled[0][2]
    last_frame = sampled[-1][2]

    # 2.1 尺度漂移（zoom）检查：Seedance 常把静帧做成缓慢推近，DINO/SSIM 看不出来，
    #     必须用特征点几何量尺度比。首帧本身是桥接 T（黑/模糊）时不可量，跳过。
    zoom_check: Dict[str, Any] = {"applicable": False, "passed": True}
    if not anchor_is_bridge:
        zoom_samples = []
        for frac, fidx, fimg in sampled[1:]:
            ratio = compute_zoom_ratio(first_frame, fimg)
            if ratio is not None:
                zoom_samples.append({"fraction": frac, "frame": fidx, "scale": round(ratio, 4)})
        max_dev = max((abs(r["scale"] - 1.0) for r in zoom_samples), default=0.0)
        zoom_threshold = float(thresh.get("zoom_max_scale_deviation", 0.04))
        # 单帧估计会被主体位移带偏；至少 2 帧同时超过阈值才判 zoom，避免误杀正常动作。
        violations = sum(1 for r in zoom_samples if abs(r["scale"] - 1.0) > zoom_threshold)
        zoom_check = {
            "applicable": bool(zoom_samples),
            "samples": zoom_samples,
            "max_scale": round(max((r["scale"] for r in zoom_samples), default=1.0), 4),
            "max_deviation": round(max_dev, 4),
            "threshold": zoom_threshold,
            "violations": violations,
            "passed": violations < 2,
        }
        if zoom_samples and not zoom_check["passed"]:
            defects.append(
                {
                    "code": "ZOOM_DRIFT",
                    "detail": (
                        f"画面尺度漂移 {max_dev:.1%}，{violations} 帧超阈值（>{zoom_threshold:.0%}）："
                        "镜头应锁死、元素屏上尺寸恒定，禁止推近/拉远"
                    ),
                    "frame": zoom_samples[-1]["frame"],
                }
            )

    # 2.2 每页屏上时长：合并段用 5s 装两页时每页只剩 2.5s；<1s 就是"刷一下"。
    seg_pages = list((pf.get("segment") or {}).get("pages") or [])
    min_page_sec = float(thresh.get("min_page_screen_sec", 1.0))
    seconds_per_page = actual_dur / max(1, len(seg_pages)) if seg_pages else actual_dur
    page_duration_check = {
        "segment_pages": seg_pages,
        "actual_duration_sec": round(actual_dur, 3),
        "seconds_per_page": round(seconds_per_page, 3),
        "threshold_sec": min_page_sec,
        "passed": seconds_per_page >= min_page_sec,
    }
    if seg_pages and seconds_per_page < min_page_sec:
        defects.append(
            {
                "code": "PAGE_TOO_SHORT",
                "detail": f"本段覆盖 {len(seg_pages)} 页，每页仅 {seconds_per_page:.2f}s < {min_page_sec}s",
                "frame": -1,
            }
        )

    # 3. 首帧保真门禁
    f0_ssim = compute_ssim_pair(kf_bgr, first_frame)
    f0_phash = compute_phash_distance(kf_bgr, first_frame)
    # pHash 在"近乎纯色"的参照帧上无意义（两张黑场也能差 32 位），此时只认 SSIM
    f0_phash_skipped = anchor_is_flat or f0_ssim >= 0.995
    f0_phash_ok = True if f0_phash_skipped else f0_phash <= thresh["first_frame_max_phash_dist"]
    first_frame_check = {
        "ssim": round(f0_ssim, 4),
        "ssim_threshold": thresh["first_frame_min_ssim"],
        "phash_distance": f0_phash,
        "phash_threshold": thresh["first_frame_max_phash_dist"],
        "phash_skipped": "参照帧近乎纯色或 SSIM 已达 0.995，pHash 不可靠" if f0_phash_skipped else None,
        "anchor_kind": "bridge_frame" if anchor_is_bridge else "keyframe",
        "passed": f0_ssim >= thresh["first_frame_min_ssim"] and f0_phash_ok,
    }
    if not first_frame_check["passed"]:
        defects.append(
            {
                "code": "FIRST_FRAME_SHIFT",
                "detail": f"首帧偏离原关键帧: SSIM={f0_ssim:.4f} (需>={thresh['first_frame_min_ssim']}), pHash={f0_phash}",
                "frame": 0,
            }
        )

    # 4. 尾帧与接缝门禁（仅 Level A 双锚定执行）
    last_target_bgr = None
    seam_check: Dict[str, Any] = {"applicable": False, "passed": True}
    if plan["relay_level"] == "A" and plan.get("last_frame_path"):
        last_target_bgr = imread_bgr(plan["last_frame_path"])
        seam_ssim = compute_ssim_pair(last_target_bgr, last_frame)
        seam_phash = compute_phash_distance(last_target_bgr, last_frame)
        min_seam = 0.85 if job["resolution"] == "480p" else thresh["seam_min_ssim"]
        max_seam_phash = int(thresh.get("seam_max_phash_dist", 6))
        target_is_flat = float(to_gray_std(last_target_bgr)) < 8.0
        seam_phash_skipped = target_is_flat or seam_ssim >= 0.995
        seam_phash_ok = True if seam_phash_skipped else seam_phash <= max_seam_phash
        seam_check = {
            "applicable": True,
            "target_next_keyframe": plan["last_frame_source"],
            "seam_ssim": round(seam_ssim, 4),
            "threshold": min_seam,
            # SSIM 对"角色局部错位、背景不变"很不敏感（实测角色偏移 165px 仍有 0.97），
            # 故接缝门禁必须同时看 pHash 距离，否则接力断裂会被放过。
            "seam_phash_distance": seam_phash,
            "seam_phash_threshold": max_seam_phash,
            "phash_skipped": "目标锚点近乎纯色（黑场 T）或 SSIM 已达 0.995，pHash 不可靠"
            if seam_phash_skipped
            else None,
            "target_kind": "bridge_frame" if last_kind == "bridge_frame" else "keyframe",
            "passed": seam_ssim >= min_seam and seam_phash_ok,
        }
        if not seam_check["passed"]:
            defects.append(
                {
                    "code": "SEAM_MISMATCH",
                    "detail": (
                        f"尾帧未走到目标帧 {plan['last_frame_source']}: SSIM={seam_ssim:.4f} "
                        f"(需>={min_seam}), pHash={seam_phash} (需<={max_seam_phash})"
                    ),
                    "frame": sampled[-1][1],
                }
            )

    # 5. 时间漂移、无中生有、文字与水印逐帧检测
    min_dino_cos = 1.0
    max_hist_dist = 0.0
    ocr_hits = []
    watermark_hits = []
    hallucination_hits = []
    ocr_degraded = False

    whitelist = pf["prompt_meta"].get("whitelist_terms", [])

    for frac, fidx, fimg in sampled:
        boxes, used_deg = text_detector.detect_text_boxes(fimg, kf_bgr)
        ocr_degraded = ocr_degraded or used_deg
        if boxes:
            ocr_hits.append({"fraction": frac, "frame": fidx, "boxes": boxes})

        if anchor_unusable:
            # 起点是桥接帧 T：画面本来就该从"黑/模糊"走到内容，逐帧比漂移没有意义
            continue
        if tail_is_bridge:
            # 段尾是导演指定的转场落点（黑场/遮挡/模糊），整段的任务就是"离开当前画面"，
            # 逐帧与首帧比一致性必然判漂移；这类段改用首帧 SSIM + 接缝 SSIM + 文字检测把关。
            continue

        d_cos = extractor.cosine_similarity(kf_bgr, fimg)
        h_dist = compute_hsv_bhattacharyya(kf_bgr, fimg)
        min_dino_cos = min(min_dino_cos, d_cos)
        max_hist_dist = max(max_hist_dist, h_dist)

        wm = detect_corner_watermark(
            fimg,
            kf_bgr,
            thresh["watermark_corner_edge_density_max"],
            float(thresh.get("watermark_corner_changed_frac_min", 0.35)),
            float(thresh.get("watermark_corner_changed_edge_min", 0.10)),
        )
        if not wm["passed"]:
            watermark_hits.append({"fraction": frac, "frame": fidx, **wm})

        hal = detect_hallucinated_objects(fimg, kf_bgr, whitelist)
        if not hal["passed"]:
            hallucination_hits.append({"fraction": frac, "frame": fidx, **hal})

    # 5.1 固定 5 帧抽样会漏掉"半秒内一闪而过"或"正在淡没"的残字，再补抽 5 帧只查文字。
    try:
        extra_text_frames = extract_video_frames(video_path, [0.10, 0.20, 0.40, 0.60, 0.90])
    except RuntimeError:
        extra_text_frames = []
    for frac, fidx, fimg in extra_text_frames:
        if any(hit["frame"] == fidx for hit in ocr_hits):
            continue
        boxes, used_deg = text_detector.detect_text_boxes(fimg, kf_bgr)
        ocr_degraded = ocr_degraded or used_deg
        if boxes:
            ocr_hits.append({"fraction": frac, "frame": fidx, "boxes": boxes})

    consistency_applicable = not (anchor_unusable or tail_is_bridge)
    consistency_skip_reason = None
    if anchor_unusable:
        consistency_skip_reason = "起点锚点是桥接帧 T / 近纯色帧，逐帧与它比必然判漂移"
    elif tail_is_bridge:
        consistency_skip_reason = "段尾是导演指定的转场落点（T），整段任务就是离开当前画面，逐帧一致性不适用"
    temporal_check = {
        "min_dino_cosine": round(min_dino_cos, 4),
        "dino_threshold": thresh["temporal_min_dino_cos"],
        "max_hist_bhattacharyya": round(max_hist_dist, 4),
        "hist_threshold": thresh["temporal_max_hist_bhattacharyya"],
        "visual_backbone_degraded": extractor.degraded,
        "applicable": consistency_applicable,
        "skipped": consistency_skip_reason,
        "passed": True
        if not consistency_applicable
        else (
            min_dino_cos >= thresh["temporal_min_dino_cos"]
            and max_hist_dist <= thresh["temporal_max_hist_bhattacharyya"]
        ),
    }
    if not temporal_check["passed"]:
        defects.append(
            {
                "code": "TEMPORAL_DRIFT",
                "detail": f"时间序列漂移: min_dino_cos={min_dino_cos:.4f}, max_hist_dist={max_hist_dist:.4f}",
                "frame": -1,
            }
        )

    if ocr_hits:
        defects.append(
            {
                "code": "TEXT_DETECTED",
                "detail": f"视频帧检出文字/字幕共 {len(ocr_hits)} 帧命中",
                "frame": ocr_hits[0]["frame"],
                "samples": ocr_hits[:2],
            }
        )

    if watermark_hits:
        defects.append(
            {
                "code": "WATERMARK_DETECTED",
                "detail": f"角落检出疑似平台水印共 {len(watermark_hits)} 帧命中",
                "frame": watermark_hits[0]["frame"],
                "samples": watermark_hits[:2],
            }
        )

    if hallucination_hits:
        defects.append(
            {
                "code": "HALLUCINATED_OBJECT",
                "detail": f"检出白名单外新增前景物体 (max_area={hallucination_hits[0]['max_new_blob_area_ratio']})",
                "frame": hallucination_hits[0]["frame"],
            }
        )

    # 生成 contact_sheet.jpg
    contact_path = page_dir / "contact_sheet.jpg"
    build_contact_sheet(kf_bgr, sampled, contact_path, last_target_bgr=last_target_bgr)

    # 提取中心帧亮度/饱和度供全片跨页离群检测
    mid_hsv = cv2.cvtColor(sampled[2][2], cv2.COLOR_BGR2HSV)
    mean_brightness = float(np.mean(mid_hsv[:, :, 2]))
    mean_saturation = float(np.mean(mid_hsv[:, :, 1]))

    verdict = "PASS" if len(defects) == 0 else "FAIL"
    qc_result = {
        "page_id": pid,
        "attempt": job["attempt"],
        "verdict": verdict,
        "degraded_detectors": {
            "visual_feature_degraded": extractor.degraded,
            "ocr_detector_degraded": ocr_degraded,
        },
        "checks": {
            "structure": structure_check,
            "first_frame": first_frame_check,
            "seam": seam_check,
            "temporal_consistency": temporal_check,
            "zoom_check": zoom_check,
            "page_duration": page_duration_check,
            "text_zero_check": {"passed": len(ocr_hits) == 0, "hits": ocr_hits},
            "watermark_check": {"passed": len(watermark_hits) == 0, "hits": watermark_hits},
            "hallucination_check": {
                "passed": len(hallucination_hits) == 0,
                "whitelist": whitelist[:20],
                "hits": hallucination_hits,
            },
        },
        "cross_page_stats": {
            "mean_brightness": round(mean_brightness, 2),
            "mean_saturation": round(mean_saturation, 2),
        },
        "defects": defects,
        "contact_sheet": str(contact_path),
    }

    (page_dir / "qc.json").write_text(json.dumps(qc_result, ensure_ascii=False, indent=2), encoding="utf-8")
    return qc_result


def decide_repair_or_escalate(
    pstate: Dict[str, Any], qc_result: Dict[str, Any], out_dir: Path, cfg: Dict[str, Any]
) -> None:
    """根据缺陷映射表决定自动返修策略或升级至 manual_review/。"""
    pid = pstate["page_id"]
    max_retries = int(cfg["repair"]["max_retries_per_page"])
    attempts = int(pstate["attempts"])
    defect_codes = {d["code"] for d in qc_result["defects"]}

    # 分析重生成影响面
    relay_level = pstate["preflight"]["generation_plan"]["relay_level"]
    blast_radius = (
        "仅限当前页 (1 页)：双锚定入口/出口均为静态关键帧，重生成不影响上下游接缝。"
        if relay_level == "A"
        else "仅限当前页 (1 页)：独立单锚定生成，刷新转场即可。"
    )

    if attempts >= max_retries:
        pstate["status"] = "ESCALATE"
        esc_dir = out_dir / "manual_review" / pid
        esc_dir.mkdir(parents=True, exist_ok=True)
        raw_clip = out_dir / pid / "raw_clip.mp4"
        contact = out_dir / pid / "contact_sheet.jpg"
        if raw_clip.is_file():
            shutil.copy2(raw_clip, esc_dir / "failed_clip.mp4")
        if contact.is_file():
            shutil.copy2(contact, esc_dir / "contact_sheet.jpg")

        esc_report = {
            "page_id": pid,
            "status": "ESCALATED_TO_MANUAL_REVIEW",
            "total_attempts": attempts,
            "hit_defects": qc_result["defects"],
            "blast_radius_analysis": blast_radius,
            "recommended_actions": [
                "检查 contact_sheet.jpg 中标红帧的形变或字幕位置",
                "若为 SEAM_MISMATCH，建议将该边降级为 Level B (仅首帧 + 0.4s 交叉溶解)",
                "若为 TEMPORAL_DRIFT，建议在 prompt_override.txt 中降低动作幅度描述",
            ],
        }
        (esc_dir / "escalation_report.json").write_text(
            json.dumps(esc_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        append_run_log(out_dir, "page_escalated", {"page_id": pid, "attempts": attempts, "defects": list(defect_codes)})
        print(f"[ESCALATE] {pid} 达到最大返修次数 ({attempts}/{max_retries})，已移交 {esc_dir}")
        return

    # 可自动返修：确定下一轮参数变更
    pstate["status"] = "REPAIRING"
    plan = pstate["preflight"]["generation_plan"]
    repair_notes = []

    if "SEAM_MISMATCH" in defect_codes and plan["duration_sec"] == 5:
        plan["duration_sec"] = 10
        plan["needs_smooth_lock"] = True
        repair_notes.append("SEAM_MISMATCH -> 时长由 5s 升至 10s 并追加平滑过渡锁定")
    elif "TEMPORAL_DRIFT" in defect_codes and plan["duration_sec"] == 10 and relay_level != "A":
        plan["duration_sec"] = 5
        repair_notes.append("TEMPORAL_DRIFT -> 非长接力页时长由 10s 降至 5s 抑制累积漂移")
    elif "ZOOM_DRIFT" in defect_codes:
        repair_notes.append("ZOOM_DRIFT -> 保持静态构图，重组 prompt_override 中的 Camera Lock 后换 seed 重生成")
    else:
        repair_notes.append(f"缺陷 {sorted(defect_codes)} -> 递增 seed 重新生成")

    pstate["last_repair_plan"] = {
        "next_attempt": attempts + 1,
        "actions": repair_notes,
        "blast_radius": blast_radius,
    }
    append_run_log(out_dir, "page_scheduled_repair", {"page_id": pid, "plan": pstate["last_repair_plan"]})
    print(f"[REPAIR] {pid} 排入第 {attempts + 1} 次返修: {'; '.join(repair_notes)}")


def run_qc(out_dir: Path, cfg: Dict[str, Any], target_page_id: Optional[str] = None) -> Dict[str, Any]:
    state = load_pipeline_state(out_dir)
    prefer_gpu = cfg["runtime"]["prefer_gpu"]
    extractor = VisualFeatureExtractor(prefer_gpu=prefer_gpu)
    text_detector = TextDetector(prefer_gpu=prefer_gpu)

    qc_results: Dict[str, Any] = {}
    for pid, pstate in state["pages"].items():
        if target_page_id and pid != target_page_id:
            continue
        if pstate["status"] != "QC_CHECKING":
            continue

        res = inspect_single_page(pstate, out_dir, cfg, extractor, text_detector)
        qc_results[pid] = res
        pstate["latest_qc"] = res

        if res["verdict"] == "PASS":
            pstate["status"] = "PASS"
            final_clip = out_dir / pid / "clip.mp4"
            shutil.copy2(pstate["current_job"]["raw_clip_path"], final_clip)
            append_run_log(out_dir, "qc_passed", {"page_id": pid, "attempt": res["attempt"]})
            print(f"[PASS] {pid} 自检全部通过 -> {final_clip}")
        else:
            decide_repair_or_escalate(pstate, res, out_dir, cfg)

    # 跨页离群检测（针对所有 PASS 页）
    pass_pages = [p for p in state["pages"].values() if p["status"] == "PASS" and p.get("latest_qc")]
    if len(pass_pages) >= 3:
        b_vals = np.array([p["latest_qc"]["cross_page_stats"]["mean_brightness"] for p in pass_pages])
        b_mean, b_std = float(np.mean(b_vals)), float(np.std(b_vals)) + 1e-5
        z_limit = cfg["thresholds"]["cross_page_max_brightness_zscore"]
        for p in pass_pages:
            val = p["latest_qc"]["cross_page_stats"]["mean_brightness"]
            z = abs(val - b_mean) / b_std
            p["latest_qc"]["cross_page_stats"]["brightness_zscore"] = round(z, 3)
            p["latest_qc"]["cross_page_stats"]["is_outlier"] = z > z_limit

    save_pipeline_state(out_dir, state)
    return qc_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run automated video consistency & quality control gate")
    parser.add_argument("--out-dir", type=Path, default=Path("out"), help="Workspace output directory")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--page-id", type=str, default=None, help="Inspect only a specific page_id")
    args = parser.parse_args()

    cfg = load_config(args.config)
    run_qc(args.out_dir, cfg, target_page_id=args.page_id)


if __name__ == "__main__":
    main()
