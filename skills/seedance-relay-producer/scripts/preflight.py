#!/usr/bin/env python3
"""
Step 1: Preflight 预检、接力拓扑决策与成本预估。
- 校验输入文件、PNG 解码与尺寸一致性
- 拦截 upstream qa_report 不合格页 (status -> BLOCKED)
- 解析 seedance_prompt.txt 完整性
- 自动计算相邻页接力降级链 (Level A 双锚定 / Level B 软转场 / Level C 硬切)
- 输出 preflight.json 与预估费用
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from inputs.adapter import ContractSchemaError, load_and_adapt_page
from scripts.common import (
    VisualFeatureExtractor,
    append_run_log,
    check_environment_capabilities,
    find_closest_aspect_ratio,
    imread_bgr,
    load_config,
    load_env_file,
    load_pipeline_state,
    save_pipeline_state,
    sha256_text,
)

REQUIRED_PROMPT_SECTIONS = ["[Camera]", "[Subject Motion]", "[Environment", "[Negative Constraints]"]
HARD_CUT_KEYWORDS = re.compile(
    r"\b(page turn|next day|next morning|meanwhile|suddenly in another|cut to|scene change)\b|翻页|第二天|转眼|与此同时|切换场景",
    re.IGNORECASE,
)


def parse_prompt_metadata(prompt_text: str, max_len: int) -> Dict[str, Any]:
    stripped = prompt_text.strip()
    if not stripped:
        return {"valid": False, "reason": "seedance_prompt.txt 为空", "warnings": [], "whitelist_terms": []}
    if len(stripped) > max_len:
        return {
            "valid": False,
            "reason": f"提示词长度 {len(stripped)} 超过 API 上限 {max_len}",
            "warnings": [],
            "whitelist_terms": [],
        }
    warnings = []
    for sec in REQUIRED_PROMPT_SECTIONS:
        if sec.lower() not in stripped.lower():
            warnings.append(f"缺少标准段落标记: {sec}")

    # 从环境动态段解析允许出现的物理动态元素白名单
    whitelist_terms: List[str] = []
    env_match = re.search(
        r"\[Environment[^\]]*\]:?\s*(.+?)(?=\n\[|\[Style Lock\]|\[Negative Constraints\]|$)",
        stripped,
        re.IGNORECASE | re.DOTALL,
    )
    if env_match:
        env_clause = env_match.group(1)
        tokens = re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fa5]{2,}", env_clause)
        whitelist_terms = sorted(list(set(t.lower() for t in tokens)))

    return {
        "valid": True,
        "reason": None,
        "warnings": warnings,
        "sha256": sha256_text(stripped),
        "char_length": len(stripped),
        "whitelist_terms": whitelist_terms,
        "has_scene_jump_cue": bool(HARD_CUT_KEYWORDS.search(stripped)),
    }


def evaluate_relay_edge(
    curr_page: Dict[str, Any],
    next_page: Dict[str, Any],
    extractor: VisualFeatureExtractor,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """根据接力规程计算相邻两页的接力级别 (A/B/C)、时长与依据。"""
    ark_cfg = cfg["ark"]
    relay_cfg = cfg["relay"]

    w1, h1 = curr_page["width"], curr_page["height"]
    w2, h2 = next_page["width"], next_page["height"]
    ar_diff = abs((w1 / float(h1)) - (w2 / float(h2)))

    img1 = imread_bgr(curr_page["keyframe_path"])
    img2 = imread_bgr(next_page["keyframe_path"])
    dino_dist = extractor.cosine_distance(img1, img2)

    # 判断是否存在语义级场景/角色跳变
    semantic_reasons: List[str] = []
    used_proxy = False

    if curr_page.get("scene_tag") and next_page.get("scene_tag"):
        if curr_page["scene_tag"] != next_page["scene_tag"]:
            semantic_reasons.append(f"场景标签切换 ({curr_page['scene_tag']} -> {next_page['scene_tag']})")
    else:
        used_proxy = True

    c1 = set(curr_page.get("character_ids") or [])
    c2 = set(next_page.get("character_ids") or [])
    if c1 and c2 and c1.isdisjoint(c2):
        semantic_reasons.append(f"主角完全更换 ({sorted(c1)} -> {sorted(c2)})")
    elif not (c1 and c2):
        used_proxy = True

    if curr_page["prompt_meta"].get("has_scene_jump_cue") or next_page["prompt_meta"].get("has_scene_jump_cue"):
        semantic_reasons.append("提示词包含翻页/跨时间转场关键词")

    edge_basis = "代理判定 (Proxy DINOv2/特征距离)" if used_proxy else "元数据语义 + 视觉特征联合判定"

    if not ark_cfg.get("supports_last_frame", True):
        return {
            "from_page": curr_page["page_id"],
            "to_page": next_page["page_id"],
            "level": "B",
            "mode": "SINGLE_ANCHOR_XFADE",
            "duration_sec": 5,
            "transition": relay_cfg["level_b_transition"],
            "transition_sec": relay_cfg["level_b_transition_sec"],
            "dino_distance": round(dino_dist, 4),
            "aspect_ratio_diff": round(ar_diff, 5),
            "basis": edge_basis,
            "reason": "API/模型配置 supports_last_frame=false，降级为仅首帧+软转场",
            "needs_smooth_lock": False,
        }

    if ar_diff > 0.01:
        return {
            "from_page": curr_page["page_id"],
            "to_page": next_page["page_id"],
            "level": "C",
            "mode": "HARD_CUT",
            "duration_sec": 5,
            "transition": None,
            "transition_sec": 0.0,
            "dino_distance": round(dino_dist, 4),
            "aspect_ratio_diff": round(ar_diff, 5),
            "basis": edge_basis,
            "reason": f"相邻页宽高比不一致 (差值 {ar_diff:.4f} > 0.01)，强制硬切",
            "needs_smooth_lock": False,
        }

    if semantic_reasons or dino_dist > relay_cfg["dino_threshold_level_b"]:
        reasons_str = "; ".join(semantic_reasons) if semantic_reasons else f"视觉差异过大 (dist={dino_dist:.3f} > {relay_cfg['dino_threshold_level_b']})"
        return {
            "from_page": curr_page["page_id"],
            "to_page": next_page["page_id"],
            "level": "C",
            "mode": "HARD_CUT",
            "duration_sec": 5,
            "transition": None,
            "transition_sec": 0.0,
            "dino_distance": round(dino_dist, 4),
            "aspect_ratio_diff": round(ar_diff, 5),
            "basis": edge_basis,
            "reason": f"触发不接力规则：{reasons_str}",
            "needs_smooth_lock": False,
        }

    if dino_dist > relay_cfg["dino_threshold_10s"]:
        return {
            "from_page": curr_page["page_id"],
            "to_page": next_page["page_id"],
            "level": "B",
            "mode": "SINGLE_ANCHOR_XFADE",
            "duration_sec": 5,
            "transition": relay_cfg["level_b_transition"],
            "transition_sec": relay_cfg["level_b_transition_sec"],
            "dino_distance": round(dino_dist, 4),
            "aspect_ratio_diff": round(ar_diff, 5),
            "basis": edge_basis,
            "reason": f"画面变化较大 (dist={dino_dist:.3f} > {relay_cfg['dino_threshold_10s']})，双锚定易形变，降级为单锚定+软转场",
            "needs_smooth_lock": False,
        }

    # 命中 Level A 双锚定
    use_10s = dino_dist > relay_cfg["dino_threshold_5s"]
    dur = 10 if use_10s else 5
    return {
        "from_page": curr_page["page_id"],
        "to_page": next_page["page_id"],
        "level": "A",
        "mode": "DUAL_ANCHOR",
        "duration_sec": dur,
        "transition": None,
        "transition_sec": 0.0,
        "dino_distance": round(dino_dist, 4),
        "aspect_ratio_diff": round(ar_diff, 5),
        "basis": edge_basis,
        "reason": (
            f"满足双锚定条件 (dist={dino_dist:.3f})，差异中等自动选用 10s 平滑过渡"
            if use_10s
            else f"满足双锚定条件 (dist={dino_dist:.3f} <= {relay_cfg['dino_threshold_5s']})，选用 5s 标准双锚定"
        ),
        "needs_smooth_lock": use_10s,
    }


def run_preflight(
    pages_dir: Path,
    out_dir: Path,
    relay_arg: str,
    resolution: str,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    thresh = cfg["thresholds"]
    ark_cfg = cfg["ark"]
    pricing = cfg["pricing"]

    page_dirs = sorted([p for p in pages_dir.iterdir() if p.is_dir()])
    if not page_dirs:
        raise RuntimeError(f"No page directories found under {pages_dir}")

    evaluated_pages: List[Dict[str, Any]] = []
    for idx, pdir in enumerate(page_dirs):
        try:
            adapted = load_and_adapt_page(pdir)
        except ContractSchemaError as exc:
            evaluated_pages.append(
                {
                    "page_id": pdir.name,
                    "page_index": idx,
                    "status": "BLOCKED",
                    "block_reason": str(exc),
                }
            )
            continue

        block_reasons: List[str] = []
        # 1. 校验 PNG 可解码及实际物理分辨率
        try:
            with Image.open(adapted["keyframe_path"]) as im:
                im.verify()
            with Image.open(adapted["keyframe_path"]) as im2:
                real_w, real_h = im2.size
                if im2.mode not in ("RGB", "RGBA"):
                    block_reasons.append(f"不支持的图像色彩模式: {im2.mode}")
        except Exception as exc:
            real_w, real_h = -1, -1
            block_reasons.append(f"clean_keyframe.png 解码失败: {exc}")

        if (real_w, real_h) != (adapted["width"], adapted["height"]):
            block_reasons.append(
                f"实际图片尺寸 ({real_w}x{real_h}) 与 manifest ({adapted['width']}x{adapted['height']}) 不一致"
            )

        # 2. 校验上游 QA 门禁
        uqa = adapted["upstream_qa"]
        if str(uqa["verdict"]).upper() != "PASS":
            block_reasons.append(f"上游 qa_report 判定未通过: verdict={uqa['verdict']}")
        if uqa["ocr_residual"] > 0:
            block_reasons.append(f"上游仍有文字残留: ocr_residual={uqa['ocr_residual']}")
        if uqa["non_edited_ssim"] < thresh["upstream_min_ssim"]:
            block_reasons.append(
                f"上游非编辑区 SSIM 不达标: {uqa['non_edited_ssim']} < {thresh['upstream_min_ssim']}"
            )
        if uqa["protected_intrusion"] > thresh["upstream_max_intrusion_px"]:
            block_reasons.append(
                f"上游保护带入侵: {uqa['protected_intrusion']}px > {thresh['upstream_max_intrusion_px']}px"
            )

        # 3. 校验提示词
        prompt_text = Path(adapted["prompt_path"]).read_text(encoding="utf-8")
        p_meta = parse_prompt_metadata(prompt_text, ark_cfg["max_prompt_length"])
        if not p_meta["valid"]:
            block_reasons.append(f"提示词校验失败: {p_meta['reason']}")

        target_ratio, ratio_diff = find_closest_aspect_ratio(
            adapted["width"], adapted["height"], ark_cfg["allowed_ratios"]
        )

        page_record = {
            **adapted,
            "page_index": idx,
            "status": "BLOCKED" if block_reasons else "PREFLIGHT_READY",
            "block_reasons": block_reasons,
            "prompt_meta": p_meta,
            "target_ratio": target_ratio,
            "ratio_diff": round(ratio_diff, 5),
        }
        evaluated_pages.append(page_record)

    ready_pages = [p for p in evaluated_pages if p["status"] == "PREFLIGHT_READY"]

    # 判定接力总开关
    if relay_arg == "on":
        relay_enabled = len(ready_pages) > 1
    elif relay_arg == "off":
        relay_enabled = False
    else:
        relay_enabled = len(ready_pages) > 1

    extractor = VisualFeatureExtractor(prefer_gpu=cfg["runtime"]["prefer_gpu"])
    relay_edges: List[Dict[str, Any]] = []

    # 初始化每页生成计划
    for i, page in enumerate(ready_pages):
        is_last = i == len(ready_pages) - 1
        plan: Dict[str, Any] = {
            "first_frame_source": f"{page['page_id']}/clean_keyframe.png",
            "last_frame_source": None,
            "last_frame_path": None,
            "relay_level": "INDEPENDENT",
            "duration_sec": 5,
            "is_terminal_page": is_last,
            "needs_smooth_lock": False,
            "needs_ending_lock": is_last and relay_enabled,
            "transition_after": None,
            "transition_sec": 0.0,
        }
        if relay_enabled and not is_last:
            next_page = ready_pages[i + 1]
            edge = evaluate_relay_edge(page, next_page, extractor, cfg)
            relay_edges.append(edge)
            plan["relay_level"] = edge["level"]
            plan["duration_sec"] = edge["duration_sec"]
            plan["needs_smooth_lock"] = edge["needs_smooth_lock"]
            plan["transition_after"] = edge["transition"]
            plan["transition_sec"] = edge["transition_sec"]
            if edge["level"] == "A":
                plan["last_frame_source"] = f"{next_page['page_id']}/clean_keyframe.png"
                plan["last_frame_path"] = next_page["keyframe_path"]

        page["generation_plan"] = plan

    # 成本预估计算
    total_cost = 0.0
    cost_breakdown: List[Dict[str, Any]] = []
    for page in ready_pages:
        dur = page["generation_plan"]["duration_sec"]
        price_key = f"{resolution}_{dur}s"
        unit_price = float(pricing["unit_cost"].get(price_key, 1.60))
        total_cost += unit_price
        cost_breakdown.append(
            {
                "page_id": page["page_id"],
                "resolution": resolution,
                "duration_sec": dur,
                "price_key": price_key,
                "estimated_cost": round(unit_price, 4),
            }
        )

    preflight_report = {
        "total_pages": len(evaluated_pages),
        "ready_count": len(ready_pages),
        "blocked_count": len(evaluated_pages) - len(ready_pages),
        "relay_mode_arg": relay_arg,
        "relay_enabled": relay_enabled,
        "visual_backbone_degraded": extractor.degraded,
        "cost_estimate": {
            "currency": pricing["currency"],
            "resolution": resolution,
            "total_estimated_cost": round(total_cost, 4),
            "budget_guard_limit": pricing["budget_guard_limit"],
            "exceeds_budget_guard": total_cost > pricing["budget_guard_limit"],
            "items": cost_breakdown,
        },
        "relay_edges": relay_edges,
        "pages": evaluated_pages,
    }

    (out_dir / "preflight.json").write_text(
        json.dumps(preflight_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 同步初始化/更新状态机
    state = load_pipeline_state(out_dir)
    state["meta"]["relay_enabled"] = relay_enabled
    state["meta"]["target_resolution"] = resolution
    state["relay_edges"] = relay_edges
    for p in evaluated_pages:
        pid = p["page_id"]
        existing = state["pages"].get(pid, {})
        # 若该页已经 QC 通过且输入未变，保留其 PASS 状态实现幂等
        if existing.get("status") == "PASS":
            continue
        state["pages"][pid] = {
            "page_id": pid,
            "status": p["status"],
            "attempts": existing.get("attempts", 0),
            "preflight": p,
            "history": existing.get("history", []),
        }
    save_pipeline_state(out_dir, state)
    append_run_log(
        out_dir,
        "preflight_completed",
        {
            "ready": len(ready_pages),
            "blocked": len(evaluated_pages) - len(ready_pages),
            "relay_enabled": relay_enabled,
            "estimated_cost": round(total_cost, 4),
        },
    )
    return preflight_report


def run_preflight_from_plan(
    plan_path: Path,
    pages_dir: Path,
    out_dir: Path,
    resolution: str,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """按导演计划（scene_plan.json）预检：一个 segment 当一条“页记录”。

    与 run_preflight 的区别只有三处：
      1. 入口从 pages/ 换成 segments[]；
      2. 接力级别恒为 A、时长取段时长——转场方式已由导演决定，这里不再判 A/B/C；
      3. 任何一段被阻断都不允许“先出一部分”，因为整本要一次拼成一条。
    """
    from inputs.plan_adapter import PlanContractError, build_plan_records

    out_dir.mkdir(parents=True, exist_ok=True)
    pricing = cfg["pricing"]

    try:
        built = build_plan_records(plan_path, pages_dir, cfg)
    except PlanContractError as exc:
        print("=== Plan preflight 阻断 ===", file=sys.stderr)
        for problem in exc.problems:
            print(f"[BLOCKED] {problem}", file=sys.stderr)
        raise

    records: List[Dict[str, Any]] = built["records"]
    ready_pages = [r for r in records if r["status"] == "PREFLIGHT_READY"]

    total_cost = 0.0
    cost_breakdown: List[Dict[str, Any]] = []
    for record in ready_pages:
        dur = record["generation_plan"]["duration_sec"]
        price_key = f"{resolution}_{dur}s"
        unit_price = float(pricing["unit_cost"].get(price_key, 1.60))
        total_cost += unit_price
        cost_breakdown.append(
            {
                "page_id": record["page_id"],
                "resolution": resolution,
                "duration_sec": dur,
                "price_key": price_key,
                "estimated_cost": round(unit_price, 4),
            }
        )

    relay_edges: List[Dict[str, Any]] = []
    for i in range(len(records) - 1):
        plan = records[i]["generation_plan"]
        relay_edges.append(
            {
                "from_page": records[i]["page_id"],
                "to_page": records[i + 1]["page_id"],
                "level": "A",
                "mode": "PLAN_ANCHOR",
                "duration_sec": plan["duration_sec"],
                "transition": plan["transition_after"],
                "transition_sec": plan["transition_sec"],
                "basis": "导演计划（scene_plan.json）",
                "reason": "首尾帧由分镜指定，不在此重判",
                "needs_smooth_lock": False,
            }
        )

    preflight_report = {
        "mode": "PLAN",
        "plan_path": built["plan_path"],
        "pages_dir": built["pages_dir"],
        "master_ratio": built["master_ratio"],
        "total_pages": len(records),
        "ready_count": len(ready_pages),
        "blocked_count": len(records) - len(ready_pages),
        "ready_for_generation": len(ready_pages) == len(records),
        "relay_mode_arg": "plan",
        "relay_enabled": True,
        "visual_backbone_degraded": False,
        "warnings": built["warnings"],
        "cost_estimate": {
            "currency": pricing["currency"],
            "resolution": resolution,
            "total_estimated_cost": round(total_cost, 4),
            "budget_guard_limit": pricing["budget_guard_limit"],
            "exceeds_budget_guard": total_cost > pricing["budget_guard_limit"],
            "items": cost_breakdown,
        },
        "relay_edges": relay_edges,
        "pages": records,
    }

    (out_dir / "preflight.json").write_text(
        json.dumps(preflight_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    state = load_pipeline_state(out_dir)
    state["meta"]["mode"] = "PLAN"
    state["meta"]["plan_path"] = built["plan_path"]
    state["meta"]["relay_enabled"] = True
    state["meta"]["target_resolution"] = resolution
    state["relay_edges"] = relay_edges
    for record in records:
        pid = record["page_id"]
        existing = state["pages"].get(pid, {})
        if existing.get("status") == "PASS":
            continue
        state["pages"][pid] = {
            "page_id": pid,
            "status": record["status"],
            "attempts": existing.get("attempts", 0),
            "preflight": record,
            "history": existing.get("history", []),
        }
    save_pipeline_state(out_dir, state)
    append_run_log(
        out_dir,
        "preflight_from_plan_completed",
        {
            "plan": built["plan_path"],
            "ready": len(ready_pages),
            "blocked": len(records) - len(ready_pages),
            "estimated_cost": round(total_cost, 4),
        },
    )
    return preflight_report


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(description="Preflight validation, relay planning & cost estimation")
    parser.add_argument("--pages-dir", type=Path, default=None, help="Input directory of page_### folders")
    parser.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="按导演计划预检（scene_plan.json，段=一条片）；页目录默认取计划的 ../pages，可用 --pages-dir 覆盖",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("out"), help="Output workspace directory")
    parser.add_argument("--relay", choices=["auto", "on", "off"], default="auto", help="Relay mode switch")
    parser.add_argument("--resolution", choices=["480p", "720p", "1080p"], default="1080p", help="Target video resolution")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Run preflight & print cost summary without generating")
    parser.add_argument("--check-env", action="store_true", help="Check local Python, GPU, FFmpeg, and API key status")
    args = parser.parse_args()

    if args.check_env:
        env_info = check_environment_capabilities()
        print(json.dumps(env_info, ensure_ascii=False, indent=2))
        sys.exit(0)

    cfg = load_config(args.config)

    if args.plan is not None:
        pages_dir = args.pages_dir or (Path(args.plan).resolve().parent.parent / "pages")
        report = run_preflight_from_plan(args.plan, pages_dir, args.out_dir, args.resolution, cfg)
    else:
        report = run_preflight(args.pages_dir or Path("pages"), args.out_dir, args.relay, args.resolution, cfg)

    ce = report["cost_estimate"]
    print("=== Preflight 完成 ===" if report.get("mode") != "PLAN" else "=== Preflight 完成（计划模式：一段一条片）===")
    if report.get("mode") == "PLAN":
        print(f"计划: {report['plan_path']}")
        print(f"页目录: {report['pages_dir']}")
    print(f"总页数: {report['total_pages']} | 就绪: {report['ready_count']} | 阻断(BLOCKED): {report['blocked_count']}")
    print(f"接力模式: {'开启 (ON)' if report['relay_enabled'] else '关闭 (OFF)'} | 接力边数: {len(report['relay_edges'])}")
    print(f"预估成本 ({ce['resolution']}): {ce['total_estimated_cost']} {ce['currency']} (熔断阈值: {ce['budget_guard_limit']})")
    if report["blocked_count"] > 0:
        for p in report["pages"]:
            if p["status"] == "BLOCKED":
                print(f"  [BLOCKED] {p['page_id']}: {p.get('block_reasons') or p.get('block_reason')}")

    for w in report.get("warnings") or []:
        print(f"  [warn] {w}")

    if report.get("mode") == "PLAN" and not report.get("ready_for_generation"):
        print(
            "[preflight] 有段被阻断：整本一次生成不允许只出一部分，修完再跑。",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
