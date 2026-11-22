#!/usr/bin/env python3
"""
Step 2: 火山方舟 Ark Seedance 异步任务提交引擎。
- 严格遵循不改写原 seedance_prompt.txt 原则；需锁定时输出独立 prompt_override.txt + unified diff + 理由
- 支持 --dry-run 模拟与单页返修定向提交 (--page-id)
- 幂等断点续跑 + 超预算强制 --yes 门禁
"""
import argparse
import difflib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.common import (
    append_run_log,
    encode_image_to_data_url,
    load_config,
    load_env_file,
    load_pipeline_state,
    save_pipeline_state,
    sha256_text,
    utc_now_iso,
)

SMOOTH_LOCK_CLAUSE = (
    ", smooth continuous motion transition between start and end composition, "
    "maintaining rigid anatomical and architectural structure without morphing"
)
ENDING_LOCK_CLAUSE = (
    ", gentle natural deceleration at the end of the sequence, holding final composition steadily"
)


def build_final_prompt_and_diff(
    page_state: Dict[str, Any],
    page_out_dir: Path,
    resolution: str,
    seed: int,
    repair_clause: Optional[str] = None,
    ark_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构造发送给 Ark 的最终文本指令，严禁修改上游 seedance_prompt.txt 原文。
    若需要额外锁定词，独立写入 prompt_override.txt 并生成 diff 与理由。

    ark_cfg.ratio_follows_first_frame=true 时不下发 --ratio：
    Seedance 2.5 的首帧/首尾帧任务要求输出比例跟随首帧，显式传 --ratio 会直接 400
    （InvalidParameter.TaskTypeConstraint）。
    """
    ark_cfg = ark_cfg or {}
    pf = page_state["preflight"]
    plan = pf["generation_plan"]
    orig_prompt_path = Path(pf["prompt_path"])
    orig_text = orig_prompt_path.read_text(encoding="utf-8").strip()
    orig_hash = sha256_text(orig_text)

    extra_clauses = []
    reasons = []
    if plan.get("needs_smooth_lock"):
        extra_clauses.append(SMOOTH_LOCK_CLAUSE)
        reasons.append("Level A 中等差异 (0.20 < DINO <= 0.35)，追加平滑过渡防形变锁定")
    if plan.get("needs_ending_lock"):
        extra_clauses.append(ENDING_LOCK_CLAUSE)
        reasons.append("接力序列末页无尾帧锚定，追加末尾自然收束锁定")
    if repair_clause:
        extra_clauses.append(repair_clause)
        reasons.append(f"返修闭环追加结构锁定: {repair_clause.strip(', ')}")

    creative_body = orig_text
    override_file = None
    diff_file = None

    if extra_clauses:
        creative_body = orig_text + "".join(extra_clauses)
        page_out_dir.mkdir(parents=True, exist_ok=True)
        override_path = page_out_dir / "prompt_override.txt"
        diff_path = page_out_dir / "prompt_override.diff"
        override_path.write_text(creative_body + "\n", encoding="utf-8")

        diff_lines = list(
            difflib.unified_diff(
                orig_text.splitlines(keepends=True),
                creative_body.splitlines(keepends=True),
                fromfile="seedance_prompt.txt (original)",
                tofile="prompt_override.txt (locked)",
            )
        )
        diff_content = (
            f"# Override Reasons: {'; '.join(reasons)}\n"
            f"# Original SHA256: {orig_hash}\n"
            + "".join(diff_lines)
        )
        diff_path.write_text(diff_content, encoding="utf-8")
        override_file = str(override_path)
        diff_file = str(diff_path)

    ratio_part = "" if ark_cfg.get("ratio_follows_first_frame") else f" --ratio {pf['target_ratio']}"
    param_suffix = (
        f" --resolution {resolution}"
        f"{ratio_part}"
        f" --duration {plan['duration_sec']}"
        f" --seed {seed}"
        f" --watermark false"
    )
    full_prompt = creative_body + param_suffix

    return {
        "original_prompt_sha256": orig_hash,
        "param_suffix": param_suffix,
        "full_prompt": full_prompt,
        "override_file": override_file,
        "diff_file": diff_file,
        "override_reasons": reasons,
    }


def submit_ark_task(
    payload: Dict[str, Any],
    base_url: str,
    api_key: str,
    timeout_sec: int,
) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/contents/generations/tasks"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    for attempt in range(1, 6):
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout_sec)
        if resp.status_code == 429:
            wait_sec = min(2 ** attempt, 30)
            time.sleep(wait_sec)
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"Ark API Error HTTP {resp.status_code}: {resp.text}")
        return resp.json()
    raise RuntimeError("Ark API Error: Exceeded max retries on HTTP 429 rate limit")


def run_generate(
    out_dir: Path,
    resolution: str,
    cfg: Dict[str, Any],
    dry_run: bool = False,
    yes: bool = False,
    target_page_id: Optional[str] = None,
    repair_clause: Optional[str] = None,
) -> None:
    state = load_pipeline_state(out_dir)
    if not state.get("pages"):
        raise RuntimeError("Pipeline state empty. Run scripts/preflight.py first.")

    ark_cfg = cfg["ark"]
    repair_cfg = cfg["repair"]
    pricing = cfg["pricing"]

    # 筛选待生成页面
    candidates = []
    for pid, pstate in state["pages"].items():
        if target_page_id and pid != target_page_id:
            continue
        if pstate["status"] in ("BLOCKED", "PASS", "ESCALATE"):
            continue
        if pstate["status"] in ("PREFLIGHT_READY", "REPAIRING"):
            candidates.append(pstate)

    if not candidates:
        print("没有需要提交生成任务的页面（均已 PASS / BLOCKED / ESCALATE 或正在轮询中）。")
        return

    # 预算核查
    est_cost = 0.0
    for pstate in candidates:
        dur = pstate["preflight"]["generation_plan"]["duration_sec"]
        est_cost += float(pricing["unit_cost"].get(f"{resolution}_{dur}s", 1.60))

    if est_cost > pricing["budget_guard_limit"] and not yes and not dry_run:
        raise RuntimeError(
            f"[BUDGET GUARD] 本次预计消耗 {est_cost:.2f} {pricing['currency']}，超过阈值 "
            f"{pricing['budget_guard_limit']} {pricing['currency']}。确认执行请追加 --yes 参数。"
        )

    api_key = os.environ.get("ARK_API_KEY", "")
    if not dry_run and not api_key:
        raise RuntimeError("环境变量 ARK_API_KEY 未设置，禁止发起真实 API 请求。")

    for pstate in candidates:
        pid = pstate["page_id"]
        pf = pstate["preflight"]
        plan = pf["generation_plan"]
        attempt_idx = int(pstate.get("attempts", 0))
        seed = int(repair_cfg["base_seed"]) + int(pf["page_index"]) * int(repair_cfg["seed_step"]) + attempt_idx

        page_out_dir = out_dir / pid
        page_out_dir.mkdir(parents=True, exist_ok=True)

        prompt_bundle = build_final_prompt_and_diff(
            pstate, page_out_dir, resolution, seed, repair_clause, ark_cfg
        )

        content_items = [{"type": "text", "text": prompt_bundle["full_prompt"]}]
        if not dry_run:
            first_url = encode_image_to_data_url(Path(pf["keyframe_path"]))
            content_items.append(
                {"type": "image_url", "image_url": {"url": first_url}, "role": "first_frame"}
            )
            if plan.get("last_frame_path"):
                last_url = encode_image_to_data_url(Path(plan["last_frame_path"]))
                content_items.append(
                    {"type": "image_url", "image_url": {"url": last_url}, "role": "last_frame"}
                )

        payload = {"model": ark_cfg["model"], "content": content_items}
        # 绘本成片需要纯净画面：Seedance 2.5 默认 generate_audio=true，会自带音轨，
        # 拼接时与后续字幕/配音冲突，显式关掉（可在 config 里改回 true）。
        if "generate_audio" in ark_cfg:
            payload["generate_audio"] = bool(ark_cfg["generate_audio"])
        if ark_cfg.get("ratio_follows_first_frame"):
            # 2.5 首帧任务不接受 ratio 字段；显式传 adaptive 亦非官方写法，直接省略
            payload.pop("ratio", None)

        if dry_run:
            print(
                f"[DRY-RUN] {pid}: mode={plan['relay_level']}, dur={plan['duration_sec']}s, "
                f"ratio={pf['target_ratio']}, res={resolution}, seed={seed}, "
                f"first={plan['first_frame_source']}, last={plan['last_frame_source']}"
            )
            continue

        resp_data = submit_ark_task(payload, ark_cfg["base_url"], api_key, ark_cfg["request_timeout_sec"])
        task_id = resp_data.get("id") or (resp_data.get("data") or {}).get("id")
        if not task_id:
            raise RuntimeError(f"Ark API 未返回有效 task_id: {resp_data}")

        unit_price = float(pricing["unit_cost"].get(f"{resolution}_{plan['duration_sec']}s", 1.60))
        job_record = {
            "attempt": attempt_idx + 1,
            "task_id": task_id,
            "submitted_at": utc_now_iso(),
            "model": ark_cfg["model"],
            "resolution": resolution,
            "ratio": pf["target_ratio"],
            "duration_sec": plan["duration_sec"],
            "seed": seed,
            "relay_level": plan["relay_level"],
            "first_frame_source": plan["first_frame_source"],
            "last_frame_source": plan["last_frame_source"],
            "original_prompt_sha256": prompt_bundle["original_prompt_sha256"],
            "param_suffix": prompt_bundle["param_suffix"],
            "override_file": prompt_bundle["override_file"],
            "diff_file": prompt_bundle["diff_file"],
            "estimated_cost": unit_price,
            "task_status": "queued",
        }

        pstate["status"] = "POLLING"
        pstate["attempts"] = attempt_idx + 1
        pstate["current_job"] = job_record
        pstate.setdefault("history", []).append(job_record)

        save_pipeline_state(out_dir, state)
        append_run_log(out_dir, "task_submitted", {"page_id": pid, "task_id": task_id, "seed": seed})
        print(f"[SUBMITTED] {pid} -> task_id={task_id} (attempt {pstate['attempts']}, seed={seed})")


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(description="Submit Seedance generation tasks to Volcengine Ark")
    parser.add_argument("--out-dir", type=Path, default=Path("out"), help="Workspace output directory")
    parser.add_argument("--resolution", choices=["480p", "720p", "1080p"], default="1080p", help="Output resolution")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--page-id", type=str, default=None, help="Run only a specific page_id")
    parser.add_argument("--dry-run", action="store_true", help="Print generation payloads without calling API")
    parser.add_argument("--yes", action="store_true", help="Confirm execution if cost exceeds budget guard")
    args = parser.parse_args()

    cfg = load_config(args.config)
    run_generate(args.out_dir, args.resolution, cfg, dry_run=args.dry_run, yes=args.yes, target_page_id=args.page_id)


if __name__ == "__main__":
    main()
