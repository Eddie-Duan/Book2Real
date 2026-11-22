#!/usr/bin/env python3
"""
build_bridge_frames.py — Step 2 of picturebook-scene-director.

Builds the shared static bridge frames T referenced by scene_plan.json
(bridge/T_###_<kind>.png). Both sides of a SHARED_T seam anchor to the SAME
file, so regenerating one segment never cascades.

Supported kinds:
    black               pure black frame
    white               pure white frame
    whip_blur           horizontal directional motion blur
    foreground_occlude  dark foreground silhouette covering ~81% of the frame
    push_detail         centre 3x detail crop (push-through anchor)

Only numpy + Pillow are required.

Usage:
    python scripts/build_bridge_frames.py --plan ./director_out/scene_plan.json \
        --pages-dir ./pages --out-dir ./director_out
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from common_io import blur_rgb, imread, imwrite, resize_rgb

VALID_KINDS = ("black", "white", "whip_blur", "foreground_occlude", "push_detail")


def directional_blur(rgb: np.ndarray, length: int = 45, steps: int = 15) -> np.ndarray:
    """Average N horizontal shifts -> a cheap, stable directional blur."""
    h, w = rgb.shape[:2]
    acc = np.zeros((h, w, 3), dtype=np.float32)
    for i in range(steps):
        frac = (i / float(steps - 1)) - 0.5
        shift = int(round(frac * length))
        acc += np.roll(rgb, shift, axis=1).astype(np.float32)
    return np.clip(acc / float(steps), 0, 255).astype(np.uint8)


def foreground_occlude(rgb: np.ndarray) -> np.ndarray:
    """压暗 + 失焦 + 暗角：读作"有东西贴近镜头/画面被遮住"，而不是一张纯色卡。

    早先的实现是用 15 分位暗色铺满 90% 画面，结果是一块平涂色块，
    在 0.2 秒的遮盖里会被观众读成"闪了一下绿屏"。
    """
    h, w = rgb.shape[:2]
    radius = max(10, int(min(h, w) / 16))
    canvas = blur_rgb(rgb, radius).astype(np.float32) * 0.26 + 10.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    r = np.sqrt(((xx - cx) / (w / 2.0)) ** 2 + ((yy - cy) / (h / 2.0)) ** 2)
    inner = 0.35
    vignette = np.clip((r - inner) / (1.0 - inner), 0.0, 1.0) ** 1.5
    canvas *= (1.0 - 0.55 * vignette)[..., None]
    return np.clip(canvas, 0, 255).astype(np.uint8)


def push_detail(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    ch, cw = max(8, h // 3), max(8, w // 3)
    y1, x1 = (h - ch) // 2, (w - cw) // 2
    crop = rgb[y1 : y1 + ch, x1 : x1 + cw]
    return blur_rgb(resize_rgb(crop, (w, h)), 3)


def make_frame(kind: str, ref_rgb: np.ndarray) -> np.ndarray:
    h, w = ref_rgb.shape[:2]
    if kind == "black":
        return np.zeros((h, w, 3), dtype=np.uint8)
    if kind == "white":
        return np.full((h, w, 3), 255, dtype=np.uint8)
    if kind == "whip_blur":
        return directional_blur(ref_rgb)
    if kind == "foreground_occlude":
        return foreground_occlude(ref_rgb)
    if kind == "push_detail":
        return push_detail(ref_rgb)
    raise ValueError(f"未知 bridge_kind: {kind!r}，合法值 {VALID_KINDS}")


def reference_page_for(plan: dict, seg_index: int) -> str:
    segs = plan.get("segments", [])
    for j in range(seg_index, -1, -1):
        pages = segs[j].get("pages") or []
        if pages:
            return pages[0]
    for j in range(seg_index + 1, len(segs)):
        pages = segs[j].get("pages") or []
        if pages:
            return pages[0]
    raise RuntimeError("scene_plan.json 里没有任何 pages，无法确定桥接帧的画风参考页")


def build_bridges(plan_path: Path, pages_dir: Path, out_dir: Path) -> dict:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    out_dir = Path(out_dir)
    bridge_dir = out_dir / "bridge"
    bridge_dir.mkdir(parents=True, exist_ok=True)

    records, seen = [], set()
    for si, seg in enumerate(plan.get("segments", [])):
        for slot in ("first_frame", "last_frame"):
            fo = seg.get(slot)
            if not fo or fo.get("kind") != "bridge_frame":
                continue
            rel = fo["source"]
            if rel in seen:
                continue
            seen.add(rel)

            kind = fo.get("bridge_kind")
            if kind not in VALID_KINDS:
                raise ValueError(f"{seg.get('segment_id')}.{slot} 的 bridge_kind 非法: {kind!r}")

            page = reference_page_for(plan, si)
            ref_path = Path(pages_dir) / page / "clean_keyframe.png"
            if not ref_path.is_file():
                raise FileNotFoundError(f"桥接帧参考页不存在: {ref_path}")
            ref = imread(ref_path)

            frame = make_frame(kind, ref)
            dest = out_dir / rel
            imwrite(dest, frame)
            records.append(
                {
                    "bridge_file": rel,
                    "bridge_kind": kind,
                    "resolution": f"{frame.shape[1]}x{frame.shape[0]}",
                    "reference_page": page,
                }
            )

    manifest = {"bridge_count": len(records), "bridges": records}
    (bridge_dir / "bridge_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build shared static bridge frames T")
    ap.add_argument("--plan", type=Path, required=True, help="scene_plan.json")
    ap.add_argument("--pages-dir", type=Path, default=Path("pages"))
    ap.add_argument("--out-dir", type=Path, default=Path("director_out"))
    args = ap.parse_args(argv)

    res = build_bridges(args.plan, args.pages_dir, args.out_dir)
    print(f"[OK] 桥接帧 {res['bridge_count']} 张 -> {Path(args.out_dir) / 'bridge'}")
    for b in res["bridges"]:
        print(f"     {b['bridge_file']}  ({b['bridge_kind']}, {b['resolution']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
