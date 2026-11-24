#!/usr/bin/env python3
"""Quantitative QA for the picture-book -> Seedance pipeline.

Hard gates (see references/qa-checklist.md):
  1. canvas lock        output size == original size
  2. non-edit integrity  block SSIM >= --ssim-min  and p99.9 pixel diff <= --p999-max
                        and share of pixels with diff > --hard-diff must be 0
  3. protected zone     (edit_mask & protected_mask) must be 0 px

SSIM uses non-overlapping 8x8 blocks (stride 8) averaged over blocks whose
valid-pixel coverage >= 50%. That is not bit-identical to skimage's sliding
window, but it is monotonic in the same way and needs only numpy.

Dependencies: numpy, Pillow only.

Usage:
  python qa_check.py --original p_00.png --final p_clean_keyframe.png \
      --text-mask p_03_text_mask.png --edit-mask p_05_edit_mask.png \
      --protected-mask p_05_protected.png --out p_qa_report.json
"""

import argparse
import json
import sys

import numpy as np
from PIL import Image

C1 = (0.01 * 255) ** 2
C2 = (0.03 * 255) ** 2


def as_mask(path, size):
    if not path:
        return None
    m = Image.open(path).convert("L")
    if m.size != size:
        raise SystemExit(f"{path}: mask size {m.size} != image size {size}")
    return np.asarray(m) > 127


def block_ssim(gray_a, gray_b, valid, block=8):
    H, W = gray_a.shape
    h, w = H // block, W // block
    if h == 0 or w == 0:
        return float("nan"), 0.0

    def blocks(x):
        return x[: h * block, : w * block].reshape(h, block, w, block)

    aa = blocks(gray_a).astype(np.float64)
    bb = blocks(gray_b).astype(np.float64)
    cov = blocks(valid.astype(np.float64)).mean(axis=(1, 3))

    ax, bx = aa.mean(axis=(1, 3)), bb.mean(axis=(1, 3))
    ax2, bx2 = (aa * aa).mean(axis=(1, 3)), (bb * bb).mean(axis=(1, 3))
    axy = (aa * bb).mean(axis=(1, 3))

    varx = np.clip(ax2 - ax * ax, 0, None)
    vary = np.clip(bx2 - bx * bx, 0, None)
    cov_xy = axy - ax * bx

    s = ((2 * ax * bx + C1) * (2 * cov_xy + C2)) / (
        (ax * ax + bx * bx + C1) * (varx + vary + C2)
    )
    sel = cov >= 0.5
    if not sel.any():
        return 1.0, 0.0
    return float(s[sel].mean()), float(sel.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", required=True)
    ap.add_argument("--final", required=True)
    ap.add_argument("--text-mask", default=None, help="Stage 3 text mask")
    ap.add_argument("--edit-mask", default=None, help="Stage 5 safe-zone mask")
    ap.add_argument("--protected-mask", default=None, help="Stage 5 protected subjects")
    ap.add_argument("--out", default=None)
    ap.add_argument("--ssim-min", type=float, default=0.9995)
    ap.add_argument("--p999-max", type=int, default=2)
    ap.add_argument("--hard-diff", type=int, default=5)
    ap.add_argument(
        "--min-text-removed",
        type=float,
        default=60.0,
        help="text mask 内至少这么大比例的像素必须被改写，否则说明根本没去字",
    )
    ap.add_argument(
        "--max-text-residual-edge",
        type=float,
        default=3.0,
        help="text mask 内'笔画型高频'像素占比上限（%%），超过视为有字形残迹",
    )
    ap.add_argument(
        "--watermark-mask",
        default=None,
        help="可选：水印/角标 mask（Stage 3 输出）；给了就校验水印区被彻底改写且无残迹",
    )
    ap.add_argument(
        "--min-watermark-removed",
        type=float,
        default=90.0,
        help="watermark mask 内至少这么大比例的像素必须被改写（水印必须清干净，不允许半透明残留）",
    )
    a = ap.parse_args()

    orig = Image.open(a.original).convert("RGB")
    final = Image.open(a.final).convert("RGB")
    size_ok = final.size == orig.size

    report = {
        "original": a.original,
        "final": a.final,
        "original_size": list(orig.size),
        "final_size": list(final.size),
        "thresholds": {
            "ssim_min": a.ssim_min,
            "p999_max": a.p999_max,
            "hard_diff": a.hard_diff,
        },
        "checks": {},
        "metrics": {},
    }

    if not size_ok:
        report["checks"]["canvas_lock"] = False
        report["verdict"] = "FAIL"
        print(f"[FAIL] canvas lock: {final.size} != {orig.size}")
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        sys.exit(1)

    o = np.asarray(orig).astype(np.int16)
    f = np.asarray(final).astype(np.int16)

    tmask = as_mask(a.text_mask, orig.size)
    emask = as_mask(a.edit_mask, orig.size)
    pmask = as_mask(a.protected_mask, orig.size)
    wmask = as_mask(a.watermark_mask, orig.size)

    union = np.zeros(orig.size[::-1], dtype=bool)
    if tmask is not None:
        union |= tmask
    if emask is not None:
        union |= emask
    valid = ~union

    report["metrics"]["text_mask_coverage_pct"] = None if tmask is None else round(float(tmask.mean() * 100), 3)
    report["metrics"]["edit_mask_coverage_pct"] = None if emask is None else round(float(emask.mean() * 100), 3)
    report["metrics"]["valid_area_pct"] = round(float(valid.mean() * 100), 3)

    if valid.sum() < 1000:
        report["checks"]["canvas_lock"] = True
        report["verdict"] = "NEEDS_HUMAN_REVIEW"
        report["note"] = "valid area < 1000 px; masks cover almost the entire page"
        print("[WARN] valid area too small, cannot judge integrity")
        if a.out:
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        sys.exit(2)

    gray_o = 0.299 * o[..., 0] + 0.587 * o[..., 1] + 0.114 * o[..., 2]
    gray_f = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    ssim, covered = block_ssim(gray_o, gray_f, valid)

    diff = np.abs(o - f).max(axis=2)
    d = diff[valid]
    p999 = float(np.percentile(d, 99.9))
    over_hard = float((d > a.hard_diff).mean() * 100)
    max_diff = int(d.max())
    changed_pct = float((diff > 0).mean() * 100)

    intrusion = 0
    if emask is not None and pmask is not None:
        intrusion = int((emask & pmask).sum())

    report["metrics"].update({
        "non_edited_ssim": round(ssim, 6),
        "ssim_block_coverage_pct": round(covered * 100, 2),
        "max_pixel_diff": max_diff,
        "pixel_diff_p999": round(p999, 2),
        "pixels_over_hard_diff_pct": round(over_hard, 4),
        "changed_pixels_pct": round(changed_pct, 3),
        "protected_zone_intrusion_px": intrusion,
    })

    checks = {
        "canvas_lock": True,
        "non_edited_ssim": bool(ssim >= a.ssim_min),
        "pixel_diff_p999": bool(p999 <= a.p999_max),
        "hard_diff_zero": bool(over_hard == 0.0),
        "protected_zone_intact": bool(intrusion == 0),
    }
    # 文字残留事后校验：文字 mask 内必须真的被改写；再用"笔画型高频"占比量残迹。
    # 只能证明 mask 区被处理过且没有明显笔画，不能替代 OCR：艺术字/水印/淡字
    # 必须由 agent 在 Stage 6 用 read_image 逐页复核，并把 ocr_residual_count=0
    # 写进 qa_report.json（preflight 会读它）。
    if tmask is not None and int(tmask.sum()) > 0:
        inside_diff = diff[tmask]
        text_removed_pct = float((inside_diff > 3).mean() * 100)
        gy, gx = np.gradient(gray_f.astype(np.float32))
        grad_mag = np.abs(gx) + np.abs(gy)
        h, w = tmask.shape
        padded = np.pad(tmask, 4, mode="edge")
        ring = np.zeros_like(tmask)
        for dy in range(9):  # 9x9 膨胀取 mask 外一圈作局部参照
            for dx in range(9):
                ring |= padded[dy : dy + h, dx : dx + w]
        ring &= ~tmask
        ring_med = float(np.median(grad_mag[ring])) if ring.any() else 0.0
        residual = float((grad_mag[tmask] > max(20.0, 3.0 * ring_med)).mean() * 100)
        report["metrics"]["text_removed_pct"] = round(text_removed_pct, 2)
        report["metrics"]["text_residual_edge_pct"] = round(residual, 3)
        checks["text_removed"] = bool(text_removed_pct >= a.min_text_removed)
        checks["text_residual_edges"] = bool(residual <= a.max_text_residual_edge)
    # 水印残留事后校验：水印必须整块被重绘（尤其半透明角标/looping 水印），
    # 只"变淡"不算去掉。同样不能替代 agent 的 read_image 逐页复核。
    if wmask is not None and int(wmask.sum()) > 0:
        w_inside = diff[wmask]
        wm_removed = float((w_inside > 3).mean() * 100)
        gy, gx = np.gradient(gray_f.astype(np.float32))
        grad_mag = np.abs(gx) + np.abs(gy)
        h, w = wmask.shape
        padded = np.pad(wmask, 4, mode="edge")
        ring = np.zeros_like(wmask)
        for dy in range(9):
            for dx in range(9):
                ring |= padded[dy : dy + h, dx : dx + w]
        ring &= ~wmask
        ring_med = float(np.median(grad_mag[ring])) if ring.any() else 0.0
        wm_residual = float((grad_mag[wmask] > max(20.0, 3.0 * ring_med)).mean() * 100)
        report["metrics"]["watermark_removed_pct"] = round(wm_removed, 2)
        report["metrics"]["watermark_residual_edge_pct"] = round(wm_residual, 3)
        checks["watermark_removed"] = bool(wm_removed >= a.min_watermark_removed)
        checks["watermark_residual_edges"] = bool(wm_residual <= a.max_text_residual_edge)
    report["checks"] = checks
    report["verdict"] = "PASS" if all(checks.values()) else "FAIL"

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

    for name, ok in checks.items():
        print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"ssim={ssim:.6f} p999={p999:.2f} max={max_diff} "
          f"over{a.hard_diff}={over_hard:.4f}% intrusion={intrusion}px -> {report['verdict']}")

    sys.exit(0 if report["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
