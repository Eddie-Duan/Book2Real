#!/usr/bin/env python3
"""
plan_scenes.py — Step 1 of picturebook-scene-director.

Reads upstream pages/page_###/ (clean_keyframe.png, page_manifest.json,
qa_report.json, seedance_prompt.txt), computes the semantic axis and the
visual axis for every adjacent pair, runs the 2x2 decision matrix, performs a
GLOBAL page -> segment split (merge / split / bridge), and writes:

    scene_plan.json
    scene_plan.md
    seam_decisions.json
    prompts/seg_###_prompt.txt

Only numpy + Pillow are required. No GPU, no model weights, no network.

Usage:
    python scripts/plan_scenes.py --pages-dir ./pages --out-dir ./director_out
    python scripts/plan_scenes.py --pages-dir ./calib_10 --calibrate
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

from common_io import (
    bhattacharyya,
    edge_bbox,
    feature_distance,
    flow_stats,
    imread,
    shape_iou,
    ssim_gray,
    to_gray,
)

ALLOWED_RATIOS = ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]
TIME_JUMP_REGEX = re.compile(
    r"\b(next day|next morning|that night|meanwhile|later|years later|suddenly|"
    r"at dawn|by sunset)\b|第二天|次日|到了晚上|转眼|与此同时|多年后|天亮|傍晚",
    re.IGNORECASE,
)
PROMPT_CHAR_LIMIT = 1000
SAFE_PROMPT_BUDGET = 950  # leave room for the parameter suffix the executor appends

# 每页在成片里至少停留这么久；低于它观众只会看到"刷一下"，属于分镜失败。
MIN_PAGE_SCREEN_SEC = 1.0

# 全片硬锁：Seedance 默认会自作主张地做 Ken-Burns 式推近，元素越拉越大。
# 这条锁每段都必须带，且放在提示词末尾（模型对末尾指令更敏感）。
CAMERA_LOCK_LINE = (
    "[Camera Lock]: locked-off static camera. No zoom in/out, no dolly, no push-in or "
    "pull-out, no pan, no tilt, no framing change; every character and prop keeps a "
    "constant on-screen size and position. The camera is locked, the action is not."
)

# 镜头锁只管机位，不管动作。Seedance 在没有动作指令时会给"微动"保平安，
# 成片看起来像 AI 偷懒；这条明确要求动作幅度与正常节奏。
MOTION_CLAUSE = (
    "[Motion]: full, clearly visible action at normal human pace — characters move limbs, "
    "torso and expression with obvious amplitude over the clip, and the action progresses "
    "to completion; no frozen pose, no micro-motion-only, no slow motion."
)

# 上游运动提示词里的推/拉/摇镜短语必须剥离，否则"镜头缓缓推近"会盖过末尾的 Camera Lock。
CAMERA_MOVE_REGEX = re.compile(
    r"(?:缓慢|慢慢|缓缓|轻轻|逐渐)?\s*(?:镜头|摄像机|相机)\s*(?:缓慢|慢慢|缓缓|轻轻|逐渐)?\s*"
    r"(?:推近|推进|拉远|后拉|推镜|拉镜|摇镜|移镜|环绕|旋转)"
    r"|(?:slowly|slow|smooth|smoothly|gentle|gently|ultra-slow)?\s*"
    r"(?:zoom|dolly|push|pull)[- ]?(?:in|out)\b"
    r"|\bken\s*burns\b",
    re.IGNORECASE,
)

# 1080p list prices of doubao-seedance-1-0-pro (CNY), used only for a rough estimate
COST_1080P = {5: 1.60, 10: 3.20}


# ------------------------------------------------------------------ helpers


def closest_legal_ratio(w: int, h: int) -> tuple:
    actual = w / float(h)
    best, best_diff = ALLOWED_RATIOS[0], float("inf")
    for r in ALLOWED_RATIOS:
        a, b = (float(x) for x in r.split(":"))
        d = abs(actual - a / b)
        if d < best_diff:
            best, best_diff = r, d
    return best, best_diff


def load_pages(pages_dir: Path) -> list:
    pages_dir = Path(pages_dir)
    page_dirs = sorted(p for p in pages_dir.iterdir() if p.is_dir())
    if not page_dirs:
        raise RuntimeError(f"{pages_dir} 下没有 page_### 目录")

    pages = []
    seen = set()
    for pdir in page_dirs:
        kf = pdir / "clean_keyframe.png"
        mf = pdir / "page_manifest.json"
        qf = pdir / "qa_report.json"
        pf = pdir / "seedance_prompt.txt"
        for req in (kf, mf, qf, pf):
            if not req.is_file():
                raise FileNotFoundError(f"缺失上游契约文件: {req}")

        manifest = json.loads(mf.read_text(encoding="utf-8"))
        qa = json.loads(qf.read_text(encoding="utf-8"))
        verdict = str(qa.get("qa_verdict") or qa.get("verdict") or "").upper()
        if verdict != "PASS":
            raise RuntimeError(f"上游质检未通过 ({pdir.name}: verdict={verdict!r})，阻断分镜规划")

        from PIL import Image

        with Image.open(kf) as im:
            w, h = im.size
        ratio_str, _ = closest_legal_ratio(w, h)

        page_id = manifest.get("page_id") or pdir.name
        if page_id in seen:
            raise RuntimeError(f"page_id 重复: {page_id}")
        seen.add(page_id)

        pages.append(
            {
                "page_id": page_id,
                "page_dir": str(pdir),
                "keyframe_rel": f"{pdir.name}/clean_keyframe.png",
                "keyframe_abs": str(kf.resolve()),
                "scene_id": manifest.get("scene_tag") or manifest.get("scene_id"),
                "time_tag": manifest.get("time_tag"),
                "character_ids": list(manifest.get("character_ids") or []),
                "width": w,
                "height": h,
                "aspect_ratio": round(w / float(h), 4),
                "legal_ratio": ratio_str,
                "prompt_text": pf.read_text(encoding="utf-8").strip(),
            }
        )
    return pages


def compute_pair_metrics(p1: dict, p2: dict) -> dict:
    a = imread(p1["keyframe_abs"])
    b = imread(p2["keyframe_abs"])
    if b.shape[:2] != a.shape[:2]:
        from common_io import resize_rgb

        b = resize_rgb(b, (a.shape[1], a.shape[0]))
    ga, gb = to_gray(a), to_gray(b)

    cxa, cya, area_a = edge_bbox(ga)
    cxb, cyb, area_b = edge_bbox(gb)
    shift = float(np.hypot(cxb - cxa, cyb - cya))
    ratio = float(area_b / max(area_a, 1e-6))
    fs = flow_stats(ga, gb)

    return {
        "feature_distance": round(feature_distance(a, b), 4),
        "ssim": round(ssim_gray(ga, gb), 4),
        "color_bhattacharyya": round(bhattacharyya(a, b), 4),
        "subject_shift_pct": round(shift, 4),
        "scale_area_ratio": round(ratio, 4),
        "flow_consistency": round(fs["consistency"], 4),
        "shape_iou": round(shape_iou(ga, gb), 4),
        "_flow_dx": round(fs["dx"], 2),
        "_flow_dy": round(fs["dy"], 2),
    }


def classify_pair(p1: dict, p2: dict, m: dict) -> dict:
    ar_diff = abs(p1["aspect_ratio"] - p2["aspect_ratio"])
    ratio_match = ar_diff <= 0.01

    # ---------------- semantic axis
    proxy_used = False
    sem_reasons = []
    if p1["scene_id"] and p2["scene_id"]:
        if p1["scene_id"] != p2["scene_id"]:
            sem_reasons.append(f"场景切换 {p1['scene_id']} -> {p2['scene_id']}")
    else:
        proxy_used = True

    c1, c2 = set(p1["character_ids"]), set(p2["character_ids"])
    if c1 and c2 and c1.isdisjoint(c2):
        sem_reasons.append("主角集合完全更换")
    elif not c1 or not c2:
        proxy_used = True

    if TIME_JUMP_REGEX.search(p2["prompt_text"]):
        sem_reasons.append("时间跳跃关键词")

    if proxy_used and not sem_reasons:
        if m["feature_distance"] > 0.35 or m["color_bhattacharyya"] > 0.22:
            sem_reasons.append("代理特征/色彩距离超阈，判为跨场或跨时段")

    semantic_continuous = not sem_reasons

    # ---------------- visual axis
    vis_fail = []
    if not ratio_match:
        vis_fail.append(f"画幅比例不一致 |Δar|={ar_diff:.3f}>0.01")
    if m["feature_distance"] > 0.35:
        vis_fail.append(f"特征距离 {m['feature_distance']}>0.35")
    if m["subject_shift_pct"] > 0.25:
        vis_fail.append(f"主体位移 {m['subject_shift_pct']:.1%}>25%")
    if not (0.67 <= m["scale_area_ratio"] <= 1.50):
        vis_fail.append(f"景别面积比 {m['scale_area_ratio']} 超出 [0.67,1.50]")
    if m["flow_consistency"] < 0.90:
        vis_fail.append(f"光流一致性 {m['flow_consistency']}<0.90")
    if m["color_bhattacharyya"] > 0.15:
        vis_fail.append(f"色彩距离 {m['color_bhattacharyya']}>0.15")

    visual_interpolable = not vis_fail
    basis = "PROXY_DETECTION" if proxy_used else "METADATA_AND_VISUAL"

    # ---------------- four quadrants
    if semantic_continuous and visual_interpolable:
        dur = 10 if (m["feature_distance"] > 0.20 or m["subject_shift_pct"] > 0.10) else 5
        return {
            "semantic_continuous": True,
            "visual_interpolable": True,
            "ratio_match": ratio_match,
            "decision": "CONTINUOUS",
            "subtype": "DUAL_ANCHOR_10S" if dur == 10 else "DUAL_ANCHOR_5S",
            "realization": "IN_CLIP",
            "recommended_duration": dur,
            "basis": basis,
            "rationale": f"语义连续且物理插值可行 -> {dur}s 双锚定同镜头。",
            "confidence": 0.90 if not proxy_used else 0.78,
            "risk_flags": [],
        }

    if semantic_continuous and not visual_interpolable:
        subtype = (
            "SHOT_SCALE_STEP"
            if not (0.67 <= m["scale_area_ratio"] <= 1.50)
            else "CUT_ON_ACTION"
        )
        return {
            "semantic_continuous": True,
            "visual_interpolable": False,
            "ratio_match": ratio_match,
            "decision": "HARD_CUT",
            "subtype": subtype,
            "realization": "IN_CLIP",
            "recommended_duration": 5,
            "basis": basis,
            "rationale": f"语义连续但不可插值（{'; '.join(vis_fail)}）-> {subtype} 0s 硬切。",
            "confidence": 0.86,
            "risk_flags": [],
        }

    if not semantic_continuous and ratio_match and (
        m["shape_iou"] >= 0.60 or m["feature_distance"] <= 0.26
    ):
        return {
            "semantic_continuous": False,
            "visual_interpolable": True,
            "ratio_match": ratio_match,
            "decision": "MATCH_CUT",
            "subtype": "GRAPHIC_MATCH",
            "realization": "IN_CLIP",
            "recommended_duration": 5,
            "basis": basis,
            "rationale": (
                f"语义跳变（{'; '.join(sem_reasons)}）但构图/形态呼应 "
                f"(shape_iou={m['shape_iou']}) -> 匹配剪辑 0s 硬切。"
            ),
            "confidence": 0.82,
            "risk_flags": [],
        }

    # quadrant IV
    is_time_lapse = any("时间" in r for r in sem_reasons) and m["color_bhattacharyya"] > 0.25
    if is_time_lapse:
        subtype, bridge_kind = "FADE_THROUGH_BLACK", "black"
    elif m["subject_shift_pct"] > 0.30:
        subtype, bridge_kind = "WHIP_PAN", "whip_blur"
    else:
        subtype, bridge_kind = "FOREGROUND_WIPE", "foreground_occlude"

    return {
        "semantic_continuous": False,
        "visual_interpolable": False,
        "ratio_match": ratio_match,
        "decision": "DESIGNED_TRANSITION",
        "subtype": subtype,
        "realization": "SHARED_T",
        "bridge_kind": bridge_kind,
        "recommended_duration": 5,
        "basis": basis,
        "rationale": (
            f"双轴跳变（{'; '.join(sem_reasons)}）-> 共享桥接帧 "
            f"{bridge_kind} 实现 {subtype}，禁用书页翻页。"
        ),
        "confidence": 0.84,
        "risk_flags": [],
    }


def strip_camera_moves(text: str) -> str:
    """剥掉上游提示词里的推/拉/摇镜短语，只删运镜词本身，不重写其余描述。"""
    cleaned = CAMERA_MOVE_REGEX.sub("", text)
    # 运镜短语删掉后常留下"at eye level"/"from a low angle"这类镜头视角残句，
    # 以及多余的分隔逗号；镜头已锁死，这些残句会和新锁冲突。
    cleaned = re.sub(r"[,\s]*\b(?:at|from)\s+(?:eye|low|high|ground|bird'?s[- ]eye|worm'?s[- ]eye) level\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[,\s]*\b(?:at|from)\s+a\s+(?:low|high|ground|bird'?s[- ]eye|worm'?s[- ]eye)\s+angle\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+([，,。；;])", r"\1", cleaned)
    cleaned = re.sub(r"([，,。；;])\s*(?:[，,。；;]\s*)+", r"\1", cleaned)
    return cleaned.strip(" ，,；;、\n")


def write_segment_prompt(prompts_dir: Path, seg_id: str, base_prompt: str, lock: str) -> str:
    prompts_dir.mkdir(parents=True, exist_ok=True)
    lock_line = f"[Director Continuity Lock]: {lock}"
    budget = SAFE_PROMPT_BUDGET - len(lock_line) - len(CAMERA_LOCK_LINE) - len(MOTION_CLAUSE) - 3
    head = strip_camera_moves(base_prompt).strip()
    if len(head) > budget:
        cut = head.rfind(" ", 0, budget)
        head = head[: cut if cut > 200 else budget].rstrip()
    text = f"{head}\n{lock_line}\n{CAMERA_LOCK_LINE}\n{MOTION_CLAUSE}\n"
    (prompts_dir / f"{seg_id}_prompt.txt").write_text(text, encoding="utf-8")
    return f"prompts/{seg_id}_prompt.txt"


# ------------------------------------------------------------------- plan


def load_scene_groups(path) -> dict:
    """读取 S0 grouper 的 scene_plan.json（按文本分场），返回 {页码数字: scene_id}。

    只用于给页设 scene_id（scene_id 不同 => classify_pair 不会判 CONTINUOUS，
    跨场页因此不会被合并进一个镜头）；分镜的其它判断仍由 S2 自己做。
    """
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    groups = {}
    for seg in data.get("segments") or data.get("scenes") or []:
        sid = seg.get("segment_id") or seg.get("scene_id")
        for pid in seg.get("pages") or []:
            digits = "".join(ch for ch in str(pid) if ch.isdigit()).lstrip("0") or "0"
            groups[digits] = sid
    return groups


def build_plan(pages_dir: Path, out_dir: Path, scene_groups=None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = out_dir / "prompts"
    pages = load_pages(Path(pages_dir))
    groups = load_scene_groups(scene_groups)
    if groups:
        for p in pages:
            digits = "".join(ch for ch in p["page_id"] if ch.isdigit()).lstrip("0") or "0"
            if digits in groups:
                p["scene_id"] = groups[digits]  # 文本分场是场景边界的唯一事实源
        missing = [p["page_id"] for p in pages
                   if ("".join(ch for ch in p["page_id"] if ch.isdigit()).lstrip("0") or "0") not in groups]
        if missing:
            raise SystemExit(f"[director] --scene-groups 缺少这些页的分场: {missing}")

    pair_evals = []
    for i in range(len(pages) - 1):
        m = compute_pair_metrics(pages[i], pages[i + 1])
        pair_evals.append({"i": i, "metrics": m, "decision": classify_pair(pages[i], pages[i + 1], m)})

    segments = []
    seams = []
    segments_meta = []  # parallel list: (start_idx, page_ids)
    idx = 0
    seg_no = 1
    while idx < len(pages):
        seg_id = f"seg_{seg_no:03d}"
        merge = (
            idx < len(pages) - 1
            and pair_evals[idx]["decision"]["decision"] == "CONTINUOUS"
        )
        if merge:
            p1, p2 = pages[idx], pages[idx + 1]
            dec = pair_evals[idx]["decision"]
            pfile = write_segment_prompt(
                prompts_dir,
                seg_id,
                p1["prompt_text"],
                "Continuous single-shot interpolation between start and end composition; "
                "rigid anatomy, no morphing, no cuts.",
            )
            seg = {
                "segment_id": seg_id,
                "pages": [p1["page_id"], p2["page_id"]],
                "segment_kind": "MERGED_PAGES",
                "scene_id": p1["scene_id"] or "sc_auto",
                "first_frame": {"source": p1["keyframe_rel"], "kind": "keyframe"},
                "last_frame": {"source": p2["keyframe_rel"], "kind": "keyframe"},
                "duration_sec": dec["recommended_duration"],
                "ratio": p1["legal_ratio"],
                "prompt_file": pfile,
                "trim_window_sec": None,
                "transition_out": {"type": "PENDING_NEXT", "realization": "NONE"},
                "fallback_if_no_last_frame": {
                    "last_frame": None,
                    "duration_sec": 5,
                    "transition_out": {
                        "type": "HARD_CUT",
                        "subtype": "CUT_ON_ACTION",
                        "realization": "IN_CLIP",
                    },
                },
                "risk_flags": [],
                "confidence": dec["confidence"],
            }
            segments.append(seg)
            segments_meta.append({"seg": seg, "start": idx, "end": idx + 1})
            seams.append(
                {
                    "seam_id": f"seam_{p1['page_id']}_{p2['page_id']}_merged",
                    "from_segment": f"{seg_id} ({p1['page_id']})",
                    "to_segment": f"{seg_id} ({p2['page_id']})",
                    "semantic_continuous": True,
                    "visual_interpolable": True,
                    "ratio_match": True,
                    "decision": "CONTINUOUS",
                    "subtype": dec["subtype"],
                    "metrics": pair_evals[idx]["metrics"],
                    "rationale": dec["rationale"],
                    "confidence": dec["confidence"],
                    "needs_human_review": False,
                }
            )
            idx += 2
        else:
            p = pages[idx]
            pfile = write_segment_prompt(
                prompts_dir,
                seg_id,
                p["prompt_text"],
                "Hold steady anatomy and clear screen direction; no text, no watermark, no morphing.",
            )
            seg = {
                "segment_id": seg_id,
                "pages": [p["page_id"]],
                "segment_kind": "SINGLE_PAGE",
                "scene_id": p["scene_id"] or "sc_auto",
                "first_frame": {"source": p["keyframe_rel"], "kind": "keyframe"},
                "last_frame": None,
                "duration_sec": 5,
                "ratio": p["legal_ratio"],
                "prompt_file": pfile,
                "trim_window_sec": None,
                "transition_out": {"type": "PENDING_NEXT", "realization": "NONE"},
                "fallback_if_no_last_frame": None,
                "risk_flags": [],
                "confidence": 0.88,
            }
            segments.append(seg)
            segments_meta.append({"seg": seg, "start": idx, "end": idx})
            idx += 1
        seg_no += 1

    # ---------------- seams between segments
    for k in range(len(segments) - 1):
        cur, nxt = segments[k], segments[k + 1]
        boundary = segments_meta[k]["end"]  # index of last page of cur
        pe = pair_evals[boundary]
        dec = dict(pe["decision"])
        metrics = pe["metrics"]
        flags = list(dec.get("risk_flags", []))

        # A CONTINUOUS pair between two SEPARATE segments = a shot that exceeds
        # one generation. Be honest: it is a hidden cut on action, not a double anchor.
        if dec["decision"] == "CONTINUOUS":
            dec = {
                **dec,
                "decision": "HARD_CUT",
                "subtype": "HIDDEN_CUT_CONTINUOUS",
                "realization": "IN_CLIP",
                "rationale": (
                    "同一镜头超过单次 5s/10s 上限，在动作峰值处拆段；"
                    "接缝为隐藏切点（动作切），不得改称双锚定同镜头。"
                ),
            }

        # SHARED_T can only attach when BOTH sides are single-page segments.
        # Attaching T to a merged segment would silently drop one of its pages.
        if dec["realization"] == "SHARED_T" and (
            cur["segment_kind"] != "SINGLE_PAGE" or nxt["segment_kind"] != "SINGLE_PAGE"
        ):
            # T 只能挂在两段单页段之间；合并段吞掉一页就不能再挂 T。
            # 但直接降成 0s 硬切正是"转场生硬"的来源，所以改由后期 0.6s 交叉溶解实现。
            dec["decision"] = "DESIGNED_TRANSITION"
            dec["subtype"] = "CROSS_DISSOLVE"
            dec["realization"] = "POST"
            dec["rationale"] += " （桥接帧目标为合并段，改由后期 0.6s 交叉溶解实现，不再纯硬切）"
            flags.append("TRANSITION_TARGET_MERGED")

        if dec["realization"] == "SHARED_T":
            bkind = dec["bridge_kind"]
            t_rel = f"bridge/T_{k + 1:03d}_{bkind}.png"
            cur["last_frame"] = {
                "source": t_rel,
                "kind": "bridge_frame",
                "bridge_kind": bkind,
            }
            nxt["first_frame"] = {
                "source": t_rel,
                "kind": "bridge_frame",
                "bridge_kind": bkind,
            }
            nxt["last_frame"] = {
                "source": pages[segments_meta[k + 1]["start"]]["keyframe_rel"],
                "kind": "keyframe",
            }
            nxt["fallback_if_no_last_frame"] = {
                "last_frame": None,
                "duration_sec": 5,
                "transition_out": {
                    "type": "DESIGNED_TRANSITION",
                    "subtype": "CROSS_DISSOLVE",
                    "realization": "POST",
                },
            }

        cur["transition_out"] = {
            "type": dec["decision"],
            "subtype": dec["subtype"],
            "realization": dec["realization"],
        }
        cur["risk_flags"] = sorted(set(cur["risk_flags"] + flags))

        seams.append(
            {
                "seam_id": f"seam_{cur['segment_id']}_{nxt['segment_id']}",
                "from_segment": cur["segment_id"],
                "to_segment": nxt["segment_id"],
                "semantic_continuous": dec["semantic_continuous"],
                "visual_interpolable": dec["visual_interpolable"],
                "ratio_match": dec["ratio_match"],
                "decision": dec["decision"],
                "subtype": dec["subtype"],
                "metrics": metrics,
                "rationale": dec["rationale"],
                "confidence": dec["confidence"],
                "needs_human_review": dec["confidence"] < 0.70,
            }
        )

    if segments:
        segments[-1]["transition_out"] = {
            "type": "END_OF_BOOK",
            "subtype": "NATURAL_HOLD",
            "realization": "NONE",
        }

    # 每页屏上时长与转场覆盖度：成片质检要能一眼看出"页码刷过去"和"全是硬切"
    for seg in segments:
        seg["page_screen_sec"] = round(seg["duration_sec"] / max(1, len(seg["pages"])), 2)
    short_pages = [s["segment_id"] for s in segments if s["page_screen_sec"] < MIN_PAGE_SCREEN_SEC]
    if short_pages:
        print(
            f"[WARN] 段 {short_pages} 每页屏上时长 < {MIN_PAGE_SCREEN_SEC}s，"
            "请拆开合并页或换 10s 时长；否则成片会'刷一下'就切页。",
            file=sys.stderr,
        )
    transition_summary = {
        "shared_t_bridge": sum(
            1 for s in segments[:-1] if s["transition_out"]["realization"] == "SHARED_T"
        ),
        "post_xfade": sum(1 for s in segments[:-1] if s["transition_out"]["realization"] == "POST"),
        "hard_cut_in_clip": sum(
            1 for s in segments[:-1] if s["transition_out"]["realization"] == "IN_CLIP"
        ),
        "min_page_screen_sec": min((s["page_screen_sec"] for s in segments), default=0.0),
    }
    transition_summary["hard_cut_ratio"] = round(
        transition_summary["hard_cut_in_clip"] / max(1, len(segments) - 1), 3
    )
    # 有效转场覆盖率：SHARED_T / POST / 独立桥接都算"观众看得见的转场"。
    # 全片 0 覆盖 = 分镜退化成幻灯片，直接标红，别让 S3 花完钱才发现。
    seam_total = max(1, len(segments) - 1)
    effective = (
        transition_summary["shared_t_bridge"]
        + transition_summary["post_xfade"]
        + sum(1 for s in segments[:-1] if s["transition_out"]["realization"] == "BRIDGE_CLIP")
    )
    transition_summary["effective_transition_count"] = effective
    transition_summary["effective_transition_ratio"] = round(effective / seam_total, 3)
    transition_gate = "PASS" if effective > 0 or len(segments) <= 1 else "FAIL"
    if transition_gate == "FAIL":
        print(
            "[WARN] 所有接缝都是 0s 硬切（effective_transition_ratio=0）："
            "分镜/桥接没有生效，成片会变成简单切屏。回 S2 补 SHARED_T 或 POST 叠化。",
            file=sys.stderr,
        )

    master_ratio = pages[0]["legal_ratio"]
    cost = sum(COST_1080P[s["duration_sec"]] for s in segments)
    plan = {
        "plan_version": "1.0.0",
        "degraded_mode": True,  # CPU proxy metrics only; no DINOv2/CLIP
        "master_ratio": master_ratio,
        "page_turn_transition_enabled": False,
        "cost_estimate_cny": round(cost, 2),
        "min_page_screen_sec": MIN_PAGE_SCREEN_SEC,
        "short_page_segments": short_pages,
        "transition_summary": transition_summary,
        "transition_gate": transition_gate,
        "pages": [
            {
                "page_id": p["page_id"],
                "scene_id": p["scene_id"] or "sc_auto",
                "time_tag": p["time_tag"],
                "character_ids": p["character_ids"],
                "ratio": p["legal_ratio"],
            }
            for p in pages
        ],
        "segments": segments,
        "seams": seams,
        "concat_plan": [
            {
                "after_segment": s["segment_id"],
                "cut": "hard" if s["transition_out"]["realization"] != "POST" else "xfade",
                "xfade_sec": 0.6 if s["transition_out"]["realization"] == "POST" else 0.0,
                "pad_to_master_ratio": s["ratio"] != master_ratio,
            }
            for s in segments[:-1]
        ],
    }

    (out_dir / "scene_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "seam_decisions.json").write_text(
        json.dumps(seams, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_plan_md(out_dir, plan, pages, segments)
    return plan


def write_plan_md(out_dir: Path, plan: dict, pages: list, segments: list) -> None:
    lines = [
        "# 绘本分镜与接缝导演规划表",
        "",
        f"- 基准画幅：`{plan['master_ratio']}`　总页数：`{len(pages)}`　"
        f"规划段数：`{len(segments)}`　预估 1080p 成本：`¥{plan['cost_estimate_cny']}`",
        "- 运行模式：`CPU 代理指标（无 DINOv2/CLIP）`　书页翻页特效：`已禁用`",
        "",
        "## 分段执行表",
        "",
        "| 段 ID | 覆盖页 | 类型 | 首帧 → 尾帧 | 时长 | 出口接缝 | 实现 |",
        "| :--- | :--- | :--- | :--- | :---: | :--- | :--- |",
    ]
    for s in segments:
        lf = s["last_frame"]["source"] if s["last_frame"] else "None"
        lines.append(
            f"| {s['segment_id']} | {', '.join(s['pages'])} | {s['segment_kind']} | "
            f"`{s['first_frame']['source']}` → `{lf}` | {s['duration_sec']}s | "
            f"{s['transition_out']['type']} / {s['transition_out'].get('subtype', '')} | "
            f"{s['transition_out']['realization']} |"
        )
    lines += ["", "## 接缝判定", "", "| 接缝 | 判定 | 关键指标 | 置信度 |", "| :--- | :--- | :--- | :---: |"]
    for seam in plan["seams"]:
        m = seam["metrics"]
        key = (
            f"feat={m.get('feature_distance')} shift={m.get('subject_shift_pct')} "
            f"scale={m.get('scale_area_ratio')} color={m.get('color_bhattacharyya')} "
            f"iou={m.get('shape_iou')}"
        )
        lines.append(
            f"| {seam['seam_id']} | {seam['decision']} / {seam.get('subtype', '')} | {key} | "
            f"{seam['confidence']} |"
        )
    (out_dir / "scene_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# -------------------------------------------------------------- calibrate


def calibrate(pages_dir: Path) -> None:
    pages = load_pages(Path(pages_dir))
    if len(pages) < 3:
        raise RuntimeError("校准至少需要 3 页（2 对相邻页）")
    rows = [compute_pair_metrics(pages[i], pages[i + 1]) for i in range(len(pages) - 1)]
    keys = [
        "feature_distance",
        "color_bhattacharyya",
        "subject_shift_pct",
        "scale_area_ratio",
        "flow_consistency",
        "shape_iou",
        "ssim",
    ]
    print(f"pages={len(pages)} pairs={len(rows)}")
    print("pair  " + "  ".join(f"{k:>18}" for k in keys))
    for i, r in enumerate(rows):
        print(f"{i:>4}  " + "  ".join(f"{r[k]:>18.4f}" for k in keys))
    print("\n分位数（用于把 INITIAL_DEFAULTS 校准到你的画风）：")
    for k in keys:
        vals = np.array([r[k] for r in rows], dtype=float)
        p10, p50, p90 = np.percentile(vals, [10, 50, 90])
        print(f"  {k:<22} P10={p10:.4f}  P50={p50:.4f}  P90={p90:.4f}")


# -------------------------------------------------------------------- main


def main(argv=None) -> int:
    # Windows 控制台默认 GBK，预估费用里的 ¥ 会直接抛 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Picturebook scene director / seam planner")
    ap.add_argument("--pages-dir", type=Path, required=True, help="上游 pages/page_### 目录")
    ap.add_argument("--out-dir", type=Path, default=Path("director_out"))
    ap.add_argument("--scene-groups", type=Path, default=None,
                    help="S0 grouper 的 scene_plan.json；用它覆盖页的 scene_id，跨场页不会被合并")
    ap.add_argument("--calibrate", action="store_true", help="只打印指标分布，不生成计划")
    args = ap.parse_args(argv)

    if args.calibrate:
        calibrate(args.pages_dir)
        return 0

    plan = build_plan(args.pages_dir, args.out_dir, args.scene_groups)
    print(
        f"[OK] {len(plan['pages'])} 页 -> {len(plan['segments'])} 段，"
        f"{len(plan['seams'])} 条接缝，预估 ¥{plan['cost_estimate_cny']}"
    )
    print(f"[OUT] {args.out_dir / 'scene_plan.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
