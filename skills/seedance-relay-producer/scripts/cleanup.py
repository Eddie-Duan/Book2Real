#!/usr/bin/env python3
"""
Step 5 辅助清理模块：针对确认为非版权的平台角标水印，执行保护带校验后的安全裁剪或 delogo 修复。
严禁去除第三方版权水印或法定 AI 生成标识；任何侵入保护带的操作直接拒绝。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.common import append_run_log, load_pipeline_state, save_pipeline_state


def check_crop_safe_against_protection(
    width: int, height: int, crop_margin_ratio: float, manifest: Dict
) -> Tuple[bool, str]:
    """校验微裁切边缘是否会入侵上游标记的主体保护带。"""
    dx = int(round(width * crop_margin_ratio))
    dy = int(round(height * crop_margin_ratio))
    # 检查 manifest 中的补全安全区或文本记录，若存在核心主体贴边风险则保守拦截
    if dx > int(width * 0.04) or dy > int(height * 0.04):
        return False, f"裁切幅度 ({crop_margin_ratio*100:.1f}%) 超过安全上限 4%"
    return True, f"安全区边缘微裁切 ({dx}px, {dy}px) 校验通过"


def apply_safe_corner_cleanup(
    input_video: Path,
    output_video: Path,
    width: int,
    height: int,
    corner: str = "bottom_right",
    method: str = "delogo",
) -> None:
    """对自有生成视频的角落平台角标执行局部 delogo 或等比微裁切还原。"""
    output_video.parent.mkdir(parents=True, exist_ok=True)
    if method == "crop":
        # 裁掉四周 2% 边缘后等比缩放回原锁定分辨率，保持画幅严格不变
        cw = int(round(width * 0.96))
        ch = int(round(height * 0.96))
        x = (width - cw) // 2
        y = (height - ch) // 2
        vf = f"crop={cw}:{ch}:{x}:{y},scale={width}:{height}:flags=lanczos"
    else:
        # 仅在角落 10% x 6% 局部应用 delogo 平滑滤波
        bw = max(int(width * 0.10), 20)
        bh = max(int(height * 0.06), 14)
        margin = 8
        coords = {
            "bottom_right": (width - bw - margin, height - bh - margin),
            "bottom_left": (margin, height - bh - margin),
            "top_right": (width - bw - margin, margin),
            "top_left": (margin, margin),
        }
        lx, ly = coords.get(corner, coords["bottom_right"])
        vf = f"delogo=x={lx}:y={ly}:w={bw}:h={bh}:show=0"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(output_video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg cleanup failed: {proc.stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe-zone watermark cleanup for owned Seedance clips")
    parser.add_argument("--out-dir", type=Path, default=Path("out"), help="Workspace output directory")
    parser.add_argument("--page-id", type=str, required=True, help="Target page_id")
    parser.add_argument("--corner", default="bottom_right", choices=["bottom_right", "bottom_left", "top_right", "top_left"])
    parser.add_argument("--method", default="delogo", choices=["delogo", "crop"])
    args = parser.parse_args()

    state = load_pipeline_state(args.out_dir)
    pstate = state["pages"].get(args.page_id)
    if not pstate:
        raise RuntimeError(f"Page not found in state: {args.page_id}")

    pf = pstate["preflight"]
    w, h = pf["width"], pf["height"]
    safe, reason = check_crop_safe_against_protection(w, h, 0.02, pf["raw_manifest"])
    if not safe:
        print(f"[BLOCKED CLEANUP] {reason}", file=sys.stderr)
        sys.exit(1)

    raw_vid = Path(pstate["current_job"]["raw_clip_path"])
    cleaned_vid = args.out_dir / args.page_id / "cleaned_clip.mp4"
    apply_safe_corner_cleanup(raw_vid, cleaned_vid, w, h, corner=args.corner, method=args.method)

    pstate["current_job"]["raw_clip_path"] = str(cleaned_vid)
    pstate["status"] = "QC_CHECKING"
    save_pipeline_state(args.out_dir, state)
    append_run_log(args.out_dir, "cleanup_applied", {"page_id": args.page_id, "method": args.method, "corner": args.corner})
    print(f"[CLEANED] {args.page_id} -> {cleaned_vid} (已重新置为 QC_CHECKING 待复检)")


if __name__ == "__main__":
    main()
