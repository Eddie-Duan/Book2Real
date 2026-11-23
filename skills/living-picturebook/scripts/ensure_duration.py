#!/usr/bin/env python3
"""把 scene_plan.json 的总时长对齐到官方区间（Q26：100–200 s）。

plan_scenes 给每页固定 5 s，一本 12 页只有 60 s，低于官方 100 s 下限。
这个脚本把段时长在允许档位（5/10 s，Seedance 支持的两档）内抬到目标时长，
不改分镜结构、不改首尾帧，只改 duration_sec。

用法：
    python ensure_duration.py --plan out/scene_plan.json --target 120
    python ensure_duration.py --plan out/scene_plan.json --target 150 --min 100 --max 200
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ALLOWED = (5, 10)


def total_of(segments: list) -> int:
    return sum(int(s.get("duration_sec") or 0) for s in segments)


def main() -> int:
    ap = argparse.ArgumentParser(description="把分镜总时长对齐到官方 100–200 s")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--target", type=int, default=120, help="目标总秒数（官方建议 100–200）")
    ap.add_argument("--min", dest="lo", type=int, default=100)
    ap.add_argument("--max", dest="hi", type=int, default=200)
    a = ap.parse_args()

    path = Path(a.plan)
    plan = json.loads(path.read_text(encoding="utf-8"))
    segs = plan.get("segments") or []
    if not segs:
        raise SystemExit("[duration] scene_plan 里没有 segments")

    before = total_of(segs)
    target = max(min(a.target, a.hi), a.lo)

    # 先升到 10 s（5→10），再按顺序加，直到 >= target
    changed = 0
    for i, seg in enumerate(segs):
        if total_of(segs) >= target:
            break
        if int(seg.get("duration_sec") or 0) < 10:
            seg["duration_sec"] = 10
            changed += 1

    after = total_of(segs)
    if after < a.lo:
        print(f"[duration] 警告：段数太少，最多抬到 {after}s，仍低于下限 {a.lo}s；"
              f"需要拆分镜头或增加页数", file=sys.stderr)
    if after > a.hi:
        print(f"[duration] 警告：总时长 {after}s 超过上限 {a.hi}s，需要下调", file=sys.stderr)

    plan.setdefault("duration_alignment", {})
    plan["duration_alignment"].update(
        {"target_sec": target, "before_sec": before, "after_sec": after,
         "segments_changed": changed, "allowed_durations": list(ALLOWED)}
    )
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ok = a.lo <= after <= a.hi
    print(f"[duration] {before}s -> {after}s（改 {changed} 段，目标 {target}s，"
          f"官方区间 {a.lo}–{a.hi}s）：{'OK' if ok else '不达标'}")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
