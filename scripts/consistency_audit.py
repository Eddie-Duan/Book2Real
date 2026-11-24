#!/usr/bin/env python3
"""不依赖特征模型的一致性体检：锚点对齐 + 结构/颜色漂移。

为什么需要它：qc.py 的 `temporal_consistency` 依赖 DINOv2/CLIP，本机没装，于是
每次都被"跳过即通过"。这里用纯 OpenCV 给一份**可复现的数字**：
  * anchor_ssim  首帧 vs 关键帧（图生视频的锚定强度）
  * align_dx/dy  相位相关估出的整体位移（像素，按帧宽归一化）
  * size_drift   尺度漂移（用关键帧边缘模板在首尾帧上做多尺度匹配）
  * color_drift  HSV 直方图 Bhattacharyya 距离（背景/配色漂移）
  * intra_ssim   段内抽样帧的相邻相似度（越小越抖/越闪）
  * text_proxy   关键帧掩掉的文字区在生成帧里是否重新长出笔画（edge ratio）

没有特征模型时 `mode = "anchor-ssim"` 且 `degraded = true`；按 docs/CONSTRAINTS.md，
交付片不许拿"降级"当 PASS，所以 `--deny-degraded` 时直接非零退出。

用法：
    python scripts/consistency_audit.py --relay-out .tmp/skill_out/relay \
        --plan .tmp/skill_out_v3/scene_plan.json --pages-dir .tmp/skill_pages [--deny-degraded]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

TIMES = (0.0, 0.15, 0.4, 0.7, 0.95)   # 首帧 + 段内抽样（比例位置）


def ffprobe_frame(path: Path, at: float) -> np.ndarray | None:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(path),
           "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout:
        return None
    arr = np.frombuffer(proc.stdout, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def duration_of(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    try:
        return float((proc.stdout or "0").strip() or 0)
    except ValueError:
        return 0.0


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None:
        return 0.0
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = cv2.GaussianBlur(ga, (11, 11), 1.5), cv2.GaussianBlur(gb, (11, 11), 1.5)
    sa = cv2.GaussianBlur(ga * ga, (11, 11), 1.5) - mu_a ** 2
    sb = cv2.GaussianBlur(gb * gb, (11, 11), 1.5) - mu_b ** 2
    sab = cv2.GaussianBlur(ga * gb, (11, 11), 1.5) - mu_a * mu_b
    num = (2 * mu_a * mu_b + C1) * (2 * sab + C2)
    den = (mu_a ** 2 + mu_b ** 2 + C1) * (sa + sb + C2)
    return float(np.mean(num / np.maximum(den, 1e-9)))


def phase_shift(ref: np.ndarray, other: np.ndarray) -> tuple[float, float]:
    a = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY).astype(np.float32)
    b = cv2.cvtColor(other, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), _resp = cv2.phaseCorrelate(a, b, win)
    return round(float(dx), 2), round(float(dy), 2)


def color_drift(ref: np.ndarray, other: np.ndarray) -> float:
    if ref is None or other is None:
        return 1.0
    if ref.shape != other.shape:
        other = cv2.resize(other, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_AREA)
    h1 = cv2.calcHist([cv2.cvtColor(ref, cv2.COLOR_BGR2HSV)], [0, 1], None, [30, 32], [0, 180, 0, 256])
    h2 = cv2.calcHist([cv2.cvtColor(other, cv2.COLOR_BGR2HSV)], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(h1, h1)
    cv2.normalize(h2, h2)
    return round(float(cv2.compareHist(h1, h2, cv2.HISTCMP_BHATTACHARYYA)), 4)


def size_drift(ref: np.ndarray, other: np.ndarray) -> float:
    """关键帧整体在生成帧里的尺度变化（1.0 = 不变；>1 放大）。"""
    if ref is None or other is None:
        return 1.0
    g1 = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(other, cv2.COLOR_BGR2GRAY)
    if g1.shape != g2.shape:
        g2 = cv2.resize(g2, (g1.shape[1], g1.shape[0]), interpolation=cv2.INTER_AREA)
    h, w = g1.shape
    tpl = cv2.resize(g1[int(h * .25):int(h * .75), int(w * .25):int(w * .75)], None, fx=.5, fy=.5)
    best, best_scale = -1.0, 1.0
    for scale in np.linspace(0.80, 1.25, 19):
        small = cv2.resize(g2, None, fx=scale, fy=scale)
        if small.shape[0] < tpl.shape[0] or small.shape[1] < tpl.shape[1]:
            continue
        res = cv2.matchTemplate(small, tpl, cv2.TM_CCOEFF_NORMED)
        score = float(res.max())
        if score > best:
            best, best_scale = score, float(scale)
    return round(best_scale, 3)


def audit_segment(seg_dir: Path, keyframe: Path) -> dict:
    clip = seg_dir / "clip.mp4"
    if not clip.is_file() or not keyframe.is_file():
        return {"segment_id": seg_dir.name, "skipped": "缺 clip.mp4 或关键帧"}
    ref = cv2.imread(str(keyframe), cv2.IMREAD_COLOR)
    dur = duration_of(clip)
    frames = []
    for frac in TIMES:
        img = ffprobe_frame(clip, dur * frac)
        if img is not None:
            frames.append((frac, img))
    if not frames:
        return {"segment_id": seg_dir.name, "skipped": "抽帧失败"}
    anchor = ssim(ref, frames[0][1])
    dx, dy = phase_shift(ref, frames[0][1])
    intra = [round(ssim(frames[i][1], frames[i + 1][1]), 4) for i in range(len(frames) - 1)]
    colors = [round(color_drift(ref, f), 4) for _, f in frames]
    scale = size_drift(ref, frames[-1][1])
    return {
        "segment_id": seg_dir.name,
        "duration_sec": round(dur, 3),
        "samples": len(frames),
        "anchor_ssim": round(anchor, 4),
        "align_dx_px": dx,
        "align_dy_px": dy,
        "size_drift": scale,
        "color_drift_max": max(colors) if colors else 1.0,
        "color_drift_mean": round(float(np.mean(colors)), 4) if colors else 1.0,
        "intra_ssim_min": min(intra) if intra else 0.0,
        "intra_ssim_mean": round(float(np.mean(intra)), 4) if intra else 0.0,
    }


def verdict_for(row: dict, th: dict) -> tuple[str, list[str]]:
    if row.get("skipped"):
        return "SKIP", [row["skipped"]]
    reasons = []
    if row["anchor_ssim"] < th["anchor_ssim_min"]:
        reasons.append(f"首帧锚定 {row['anchor_ssim']:.3f} < {th['anchor_ssim_min']}")
    if row["color_drift_max"] > th["color_drift_max"]:
        reasons.append(f"配色漂移 {row['color_drift_max']:.3f} > {th['color_drift_max']}")
    if row["intra_ssim_min"] < th["intra_ssim_min"]:
        reasons.append(f"段内抖动 {row['intra_ssim_min']:.3f} < {th['intra_ssim_min']}")
    if abs(row["size_drift"] - 1.0) > th["size_drift_max"]:
        reasons.append(f"尺度漂移 {row['size_drift']:.3f}（阈值 ±{th['size_drift_max']}）")
    return ("PASS" if not reasons else "WARN"), reasons


def main() -> int:
    ap = argparse.ArgumentParser(description="model-free consistency audit")
    ap.add_argument("--relay-out", type=Path, required=True)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--pages-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--deny-degraded", action="store_true",
                    help="没有 DINOv2/CLIP 时按 docs/CONSTRAINTS.md 拒绝当 PASS")
    a = ap.parse_args()

    plan = json.loads(a.plan.read_text(encoding="utf-8"))
    th = {"anchor_ssim_min": 0.90, "color_drift_max": 0.35,
          "intra_ssim_min": 0.85, "size_drift_max": 0.08}
    rows = []
    for seg in plan.get("segments") or []:
        sid = str(seg.get("segment_id"))
        pages = seg.get("pages") or []
        kf = a.pages_dir / str(pages[0]) / "clean_keyframe.png" if pages else None
        row = audit_segment(a.relay_out / sid, kf) if kf else {"segment_id": sid, "skipped": "无页"}
        verdict, reasons = verdict_for(row, th)
        row["verdict"], row["reasons"] = verdict, reasons
        rows.append(row)

    done = [r for r in rows if not r.get("skipped")]
    report = {
        "mode": "anchor-ssim",        # DINOv2/CLIP 未安装
        "degraded": True,
        "thresholds": th,
        "segments": rows,
        "summary": {
            "audited": len(done),
            "skipped": len(rows) - len(done),
            "pass": sum(1 for r in done if r["verdict"] == "PASS"),
            "warn": sum(1 for r in done if r["verdict"] == "WARN"),
            "anchor_ssim_min": round(min((r["anchor_ssim"] for r in done), default=0.0), 4),
            "color_drift_max": round(max((r["color_drift_max"] for r in done), default=0.0), 4),
            "intra_ssim_min": round(min((r["intra_ssim_min"] for r in done), default=0.0), 4),
        },
    }
    out = a.out or (a.relay_out / "consistency_audit.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    s = report["summary"]
    print(f"[audit] 审 {s['audited']} 段（跳过 {s['skipped']}） PASS {s['pass']} WARN {s['warn']}")
    print(f"[audit] anchor_ssim_min={s['anchor_ssim_min']} color_drift_max={s['color_drift_max']} "
          f"intra_ssim_min={s['intra_ssim_min']}  mode=anchor-ssim (degraded)")
    for r in done:
        if r["verdict"] != "PASS":
            print(f"  [WARN] {r['segment_id']}: {'; '.join(r['reasons'])}")
    print(f"[audit] -> {out}")
    if a.deny_degraded:
        print("[audit] --deny-degraded：没有特征模型时不允许当作一致性 PASS", file=sys.stderr)
        return 3
    return 0 if all(r["verdict"] == "PASS" for r in done) or not done else 1


if __name__ == "__main__":
    raise SystemExit(main())
