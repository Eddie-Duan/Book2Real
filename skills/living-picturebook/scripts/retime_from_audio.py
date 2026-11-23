#!/usr/bin/env python3
"""按实测语音时长反推页/场时长，让画面跟着声音走（重写版）。

输入：
    --narration  build_narration.py 产出的 .json（含每行 actual_sec）
    --scene-plan scene_plan.json（grouper 产出）
输出：
    --out-plan   scene_plan.retimed.json
    --out-audio  audio_plan.retimed.json（起止秒按页窗口重排）

规则：
    每页 need = Σ(实际语音) + 0.12s × (行数-1)
    每页拆成若干 5/10 秒镜头（Seedance 单条上限 10 秒）
    总时长不足 target（默认 100s）时，按页轮流追加 5 秒 continuation 镜头
    录音总长必须落在 100–200s
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

GAP = 0.12


def shots_for(need: float) -> list[int]:
    """一页的语音需求 -> 若干 5/10 秒镜头。"""
    if need <= 5:
        return [5]
    if need <= 10:
        return [10]
    n = math.ceil(need / 10.0)
    base = [10] * (n - 1)
    rest = need - 10 * (n - 1)
    base.append(5 if rest <= 5 else 10)
    return base


def main() -> int:
    ap = argparse.ArgumentParser(description="用实测语音时长反推页/场时长")
    ap.add_argument("--narration", required=True)
    ap.add_argument("--scene-plan", required=True)
    ap.add_argument("--out-plan", required=True)
    ap.add_argument("--out-audio", required=True)
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--hi", type=int, default=200)
    a = ap.parse_args()

    narr = json.loads(Path(a.narration).read_text(encoding="utf-8"))
    plan = json.loads(Path(a.scene_plan).read_text(encoding="utf-8"))
    rows = narr.get("rows") or []
    if not rows:
        raise SystemExit("[retime] narration 里没有 rows")

    # 每页的语音需求
    by_page: dict[str, list[dict]] = {}
    for r in rows:
        by_page.setdefault(r["page_id"], []).append(r)
    need = {}
    for pid, lines in by_page.items():
        lines.sort(key=lambda x: float(x["start_sec"]))
        need[pid] = round(sum(float(l["actual_sec"]) for l in lines) + GAP * (len(lines) - 1), 2)

    # 页 -> 镜头列表（顺序保持 scene_plan 里的页序）
    page_shots = {pid: shots_for(n) for pid, n in need.items()}

    scenes = []
    page_order = []
    for seg in plan.get("segments") or []:
        pages = [p for p in (seg.get("pages") or []) if p in page_shots]
        page_order.extend(pages)
        shots = []
        for pid in pages:
            shots += [{"kind": "single" if k == 0 else "continuation",
                       "pages": [pid], "duration_sec": d}
                      for k, d in enumerate(page_shots[pid])]
        scenes.append({"segment_id": seg["segment_id"], "pages": pages,
                       "character_ids": seg.get("character_ids") or [],
                       "transition_out": seg.get("transition_out") or {},
                       "shot_plan": shots,
                       "duration_sec": sum(s["duration_sec"] for s in shots)})

    total = sum(s["duration_sec"] for s in scenes)

    # 不足 100s：按页轮流追加 5 秒 continuation 镜头
    i = 0
    while total < a.target and page_order:
        pid = page_order[i % len(page_order)]
        for sc in scenes:
            mine = [s for s in sc["shot_plan"] if s["pages"][0] == pid]
            if mine:
                sc["shot_plan"].append({"kind": "continuation", "pages": [pid], "duration_sec": 5})
                sc["duration_sec"] += 5
                total += 5
                break
        i += 1
        if i > 500:
            break

    if total > a.hi:
        print(f"[retime] 警告：{total}s 超过上限 {a.hi}s", file=sys.stderr)

    # 音频重排：每页窗口 = 该页所有镜头之和
    out_lines = []
    cursor = 0.0
    for sc in scenes:
        for pid in sc["pages"]:
            win = sum(float(s["duration_sec"]) for s in sc["shot_plan"] if s["pages"][0] == pid)
            t = cursor
            for l in sorted(by_page.get(pid, []), key=lambda x: float(x["start_sec"])):
                dur = float(l["actual_sec"])
                out_lines.append({**{k: l[k] for k in ("scene_id", "page_id", "kind", "speaker",
                                                       "text", "voice", "rate", "pitch")},
                                  "start_sec": round(t, 2), "end_sec": round(t + dur, 2)})
                t += dur + GAP
            cursor += win

    over = [{"page_id": pid, "need_sec": need[pid],
             "shot_sec": sum(page_shots[pid]),
             "over_sec": round(need[pid] - sum(page_shots[pid]), 2)}
            for pid in page_order if need[pid] > sum(page_shots[pid]) + 0.05]

    new_plan = {
        "plan_version": plan.get("plan_version", "1.0.0"),
        "source": (plan.get("source") or "") + "+retimed-by-audio",
        "book_id": plan.get("book_id"),
        "lang": plan.get("lang"),
        "page_count": plan.get("page_count"),
        "scene_count": len(scenes),
        "estimated_total_sec": total,
        "segments": scenes,
        "coverage": plan.get("coverage") or {},
        "boundaries": plan.get("boundaries") or [],
        "retime": {"target": a.target, "total_sec": total,
                   "pages": [{"page_id": pid, "need_sec": need[pid],
                              "shots": page_shots[pid],
                              "shot_sec": sum(page_shots[pid])} for pid in page_order]},
    }
    Path(a.out_plan).write_text(json.dumps(new_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    new_audio = {"book_id": narr.get("book_id"), "lang": narr.get("lang"),
                 "total_sec": total, "voices": narr.get("voices") or {},
                 "lines": out_lines, "channels": narr.get("channels") or {}}
    Path(a.out_audio).write_text(json.dumps(new_audio, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    n_shots = sum(len(s["shot_plan"]) for s in scenes)
    print(f"[retime] 语音 {cursor:.1f}s -> 画面 {total}s（{len(scenes)} 场 / {n_shots} 镜），"
          f"区间 {a.target}–{a.hi}s")
    for rec in new_plan["retime"]["pages"]:
        print(f"  {rec['page_id']:<5} 语音 {rec['need_sec']:>5.2f}s  镜头 {rec['shots']}")
    if over:
        print(f"[retime] {len(over)} 页超出镜头：{over}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
