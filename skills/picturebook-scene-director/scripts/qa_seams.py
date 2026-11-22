#!/usr/bin/env python3
"""
qa_seams.py — Step 3/4 of picturebook-scene-director.

Runs seam-type-specific acceptance checks on generated clips and writes
seam_qa.json. Clips may be:
    clips/seg_001.mp4          (needs OpenCV or ffmpeg on PATH)
    clips/seg_001/*.png        (frame directory; sorted, first..last)

Checks by decision:
    CONTINUOUS (merged page seam)  generated last frame vs ground-truth keyframe
    HARD_CUT                       luminance jump + screen-direction continuity
    MATCH_CUT                      edge-mask IoU across the cut
    DESIGNED_TRANSITION (SHARED_T) both sides vs the shared T file + motion angle

Only numpy + Pillow are required for frame directories. OpenCV / ffmpeg are
optional accelerators for mp4.

Usage:
    python scripts/qa_seams.py --plan ./director_out/scene_plan.json \
        --clips-dir ./clips --pages-dir ./pages --out-dir ./director_out
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from common_io import (
    flow_vector,
    hamming,
    imread,
    load_clip_frames,
    mean_luma,
    phash,
    shape_iou,
    ssim_gray,
    to_gray,
)

VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".avi")


def resolve_clip(clips_dir: Path, seg_id: str) -> Path | None:
    d = Path(clips_dir)
    for ext in VIDEO_EXT:
        p = d / f"{seg_id}{ext}"
        if p.is_file():
            return p
    p = d / seg_id
    if p.is_dir():
        return p
    return None


def seam_metrics_for_decision(decision: str, from_frames: list, to_frames: list) -> dict:
    last_out = from_frames[-1]
    first_in = to_frames[0]
    g_out, g_in = to_gray(last_out), to_gray(first_in)

    v_out = flow_vector(to_gray(from_frames[-2]), g_out) if len(from_frames) >= 2 else np.zeros(2)
    v_in = flow_vector(g_in, to_gray(to_frames[1])) if len(to_frames) >= 2 else np.zeros(2)

    denom = float(np.linalg.norm(v_out) * np.linalg.norm(v_in)) + 1e-8
    cos = float(np.dot(v_out, v_in) / denom)
    angle = float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))
    speed_ratio = float((np.linalg.norm(v_in) + 1e-5) / (np.linalg.norm(v_out) + 1e-5))

    return {
        "ssim": round(ssim_gray(g_out, g_in), 4),
        "phash_dist": hamming(phash(g_out), phash(g_in)),
        "luminance_jump": round(abs(mean_luma(first_in) - mean_luma(last_out)), 4),
        "flow_angle_deg": round(angle, 2),
        "speed_ratio": round(speed_ratio, 3),
        "screen_dir_dot": round(float(v_out[0] * v_in[0]), 4),
        "shape_iou": round(shape_iou(g_out, g_in), 4),
        "_v_out": [round(float(v_out[0]), 2), round(float(v_out[1]), 2)],
        "_v_in": [round(float(v_in[0]), 2), round(float(v_in[1]), 2)],
    }


def evaluate(plan_path: Path, clips_dir: Path, pages_dir: Path, out_dir: Path) -> dict:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    out_dir = Path(out_dir)
    seg_map = {s["segment_id"]: s for s in plan.get("segments", [])}
    entries = []

    for seam in plan.get("seams", []):
        sid = seam["seam_id"]
        decision = seam["decision"]

        # ---- merged page seam: compare clip tail with the ground-truth keyframe
        if sid.endswith("_merged"):
            seg_id = seam["from_segment"].split()[0]
            seg = seg_map[seg_id]
            clip = resolve_clip(clips_dir, seg_id)
            if clip is None:
                entries.append(_skip(sid, decision, "CLIP_MISSING", f"缺少片段 {seg_id}"))
                continue
            lf = seg.get("last_frame") or {}
            gt = Path(pages_dir) / lf.get("source", "")
            if lf.get("kind") != "keyframe" or not gt.is_file():
                entries.append(_skip(sid, decision, "GT_MISSING", f"缺少基准帧 {gt}"))
                continue
            frames = load_clip_frames(clip)
            last = frames[-1]
            g_gt, g_last = to_gray(imread(gt)), to_gray(last)
            s = round(ssim_gray(g_gt, g_last), 4)
            pd = hamming(phash(g_gt), phash(g_last))
            ok = s >= 0.88 and pd <= 6
            entries.append(
                {
                    "seam_id": sid,
                    "decision": decision,
                    "verdict": "PASS" if ok else "FAIL",
                    "metrics": {"ssim_to_keyframe": s, "phash_dist": pd},
                    "failure_code": None if ok else "SEAM_MISMATCH",
                    "repair_action": None
                    if ok
                    else f"只重生成 {seg_id}（换 seed 或升 10s），不动上下游",
                }
            )
            continue

        from_id, to_id = seam["from_segment"], seam["to_segment"]
        c_from = resolve_clip(clips_dir, from_id)
        c_to = resolve_clip(clips_dir, to_id)
        if c_from is None or c_to is None:
            missing = from_id if c_from is None else to_id
            entries.append(_skip(sid, decision, "CLIP_MISSING", f"缺少片段 {missing}"))
            continue

        m = seam_metrics_for_decision(decision, load_clip_frames(c_from), load_clip_frames(c_to))

        if decision == "HARD_CUT":
            ok = m["luminance_jump"] <= 0.18 and m["screen_dir_dot"] >= -0.05
            code = None if ok else (
                "FLASH_FRAME_ERROR" if m["luminance_jump"] > 0.18 else "AXIS_FLIP_180"
            )
            repair = None if ok else f"重生成 {to_id}，保持 180° 屏幕方向与曝光连续"
        elif decision == "MATCH_CUT":
            ok = m["shape_iou"] >= 0.60
            code = None if ok else "MATCH_CUT_FAIL"
            repair = None if ok else "锁定匹配图形的中心坐标与大小后重生成对应单段"
        elif decision == "DESIGNED_TRANSITION":
            # Position continuity at T is the hard requirement: both sides must
            # actually land on the shared frame. Velocity mismatch is a warning.
            angle_ok = m["flow_angle_deg"] <= 45.0 or abs(m["_v_out"][0]) < 0.5
            ok = m["ssim"] >= 0.88 and angle_ok
            code = None if ok else "BRIDGE_T_MISS"
            repair = None if ok else "只重跑未抵达桥接帧 T 的那一侧（零级联）"

            t_rel = (seg_map.get(from_id, {}).get("last_frame") or {}).get("source")
            t_path = (out_dir / t_rel) if t_rel else None
            if t_path is not None and t_path.is_file():
                gt = to_gray(imread(t_path))
                s_from = ssim_gray(gt, to_gray(load_clip_frames(c_from)[-1]))
                s_to = ssim_gray(gt, to_gray(load_clip_frames(c_to)[0]))
                m["ssim_to_T"] = round(min(s_from, s_to), 4)
                ok = m["ssim_to_T"] >= 0.85
                code = None if ok else "BRIDGE_T_MISS"
                repair = None if ok else "只重跑未抵达桥接帧 T 的那一侧（零级联）"
                if ok and m["flow_angle_deg"] > 60.0:
                    m["warning"] = "VELOCITY_MISMATCH_AT_T"
        else:  # CONTINUOUS that somehow survived at segment level
            ok = m["ssim"] >= 0.88 and m["phash_dist"] <= 6
            code = None if ok else "SEAM_MISMATCH"
            repair = None if ok else "改判硬切或重生成该段"

        entries.append(
            {
                "seam_id": sid,
                "decision": decision,
                "verdict": "PASS" if ok else "FAIL",
                "metrics": m,
                "failure_code": code,
                "repair_action": repair,
            }
        )

    fails = [e for e in entries if e["verdict"] == "FAIL"]
    skips = [e for e in entries if e["verdict"] == "SKIP"]
    report = {
        "overall_verdict": "FAIL" if fails else ("PARTIAL" if skips else "PASS"),
        "total_seams": len(entries),
        "failed": len(fails),
        "skipped": len(skips),
        "seams": entries,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "seam_qa.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _skip(seam_id: str, decision: str, code: str, msg: str) -> dict:
    return {
        "seam_id": seam_id,
        "decision": decision,
        "verdict": "SKIP",
        "metrics": {},
        "failure_code": code,
        "repair_action": msg,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seam-type-specific acceptance checks")
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--clips-dir", type=Path, required=True)
    ap.add_argument("--pages-dir", type=Path, default=Path("pages"))
    ap.add_argument("--out-dir", type=Path, default=Path("director_out"))
    args = ap.parse_args(argv)

    rep = evaluate(args.plan, args.clips_dir, args.pages_dir, args.out_dir)
    print(
        f"[{rep['overall_verdict']}] seams={rep['total_seams']} "
        f"failed={rep['failed']} skipped={rep['skipped']}"
    )
    for e in rep["seams"]:
        print(f"  {e['verdict']:<4} {e['seam_id']:<28} {e['decision']:<20} {e['failure_code'] or ''}")
    return 0 if rep["overall_verdict"] != "FAIL" else 2


if __name__ == "__main__":
    sys.exit(main())
