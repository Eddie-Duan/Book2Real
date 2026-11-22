#!/usr/bin/env python3
"""
Step 3: 异步任务轮询（指数退避 + 超时上限）与视频流立即落盘。
- 防止 Ark 临时签名 URL 过期
- 自动记录实际计费条目到 cost.csv
"""
import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.common import (
    append_run_log,
    load_config,
    load_env_file,
    load_pipeline_state,
    save_pipeline_state,
    utc_now_iso,
)


def query_ark_task(task_id: str, base_url: str, api_key: str, timeout_sec: int) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/contents/generations/tasks/{task_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(url, headers=headers, timeout=timeout_sec)
    if resp.status_code >= 400:
        raise RuntimeError(f"Query task {task_id} HTTP {resp.status_code}: {resp.text}")
    return resp.json()


def download_video_stream(video_url: str, dest_path: Path, timeout_sec: int = 120) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".mp4.part")
    with requests.get(video_url, stream=True, timeout=timeout_sec) as resp:
        resp.raise_for_status()
        with tmp_path.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    fh.write(chunk)
    tmp_path.replace(dest_path)


def record_cost_entry(out_dir: Path, pid: str, job: Dict[str, Any], currency: str) -> None:
    cost_csv = out_dir / "cost.csv"
    write_header = not cost_csv.is_file()
    with cost_csv.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(
                [
                    "timestamp",
                    "page_id",
                    "attempt",
                    "task_id",
                    "model",
                    "resolution",
                    "duration_sec",
                    "relay_level",
                    "cost",
                    "currency",
                ]
            )
        writer.writerow(
            [
                utc_now_iso(),
                pid,
                job["attempt"],
                job["task_id"],
                job["model"],
                job["resolution"],
                job["duration_sec"],
                job["relay_level"],
                job["estimated_cost"],
                currency,
            ]
        )


def run_poll_and_download(
    out_dir: Path, cfg: Dict[str, Any], target_page_id: Optional[str] = None
) -> None:
    state = load_pipeline_state(out_dir)
    ark_cfg = cfg["ark"]
    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        raise RuntimeError("环境变量 ARK_API_KEY 未设置。")

    polling_pages = [
        pstate
        for pid, pstate in state["pages"].items()
        if (not target_page_id or pid == target_page_id) and pstate["status"] == "POLLING"
    ]

    if not polling_pages:
        print("当前没有处于 POLLING 状态的任务。")
        return

    for pstate in polling_pages:
        pid = pstate["page_id"]
        job = pstate["current_job"]
        task_id = job["task_id"]

        start_ts = time.time()
        interval = float(ark_cfg["poll_initial_interval_sec"])
        max_interval = float(ark_cfg["poll_max_interval_sec"])
        total_timeout = float(ark_cfg["poll_total_timeout_sec"])

        print(f"[POLLING] 开始轮询 {pid} (task_id={task_id})...")
        while True:
            if time.time() - start_ts > total_timeout:
                job["task_status"] = "timeout"
                pstate["status"] = "REPAIRING"
                save_pipeline_state(out_dir, state)
                raise TimeoutError(f"任务 {task_id} ({pid}) 轮询超时 ({total_timeout}s)")

            task_data = query_ark_task(task_id, ark_cfg["base_url"], api_key, ark_cfg["request_timeout_sec"])
            status = task_data.get("status") or (task_data.get("data") or {}).get("status")

            if status in ("queued", "running"):
                time.sleep(interval)
                interval = min(interval * 1.5, max_interval)
                continue

            if status == "succeeded":
                content_obj = task_data.get("content") or (task_data.get("data") or {}).get("content") or {}
                video_url = content_obj.get("video_url") or task_data.get("video_url")
                if not video_url:
                    raise RuntimeError(f"任务 {task_id} 状态 succeeded 但缺失 video_url: {task_data}")

                raw_clip_path = out_dir / pid / "raw_clip.mp4"
                download_video_stream(video_url, raw_clip_path)

                job["task_status"] = "succeeded"
                job["completed_at"] = utc_now_iso()
                job["raw_clip_path"] = str(raw_clip_path)
                pstate["status"] = "QC_CHECKING"

                record_cost_entry(out_dir, pid, job, cfg["pricing"]["currency"])
                save_pipeline_state(out_dir, state)
                append_run_log(out_dir, "task_downloaded", {"page_id": pid, "task_id": task_id, "path": str(raw_clip_path)})
                print(f"[DOWNLOADED] {pid} -> {raw_clip_path}")
                break

            # failed / cancelled
            err_msg = str(task_data.get("error") or task_data)
            job["task_status"] = "failed"
            job["error"] = err_msg
            pstate["status"] = "REPAIRING"
            save_pipeline_state(out_dir, state)
            append_run_log(out_dir, "task_failed", {"page_id": pid, "task_id": task_id, "error": err_msg})
            print(f"[FAILED] {pid} (task_id={task_id}): {err_msg}", file=sys.stderr)
            break


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(description="Poll Ark Seedance tasks and download videos immediately")
    parser.add_argument("--out-dir", type=Path, default=Path("out"), help="Workspace output directory")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--page-id", type=str, default=None, help="Poll only a specific page_id")
    args = parser.parse_args()

    cfg = load_config(args.config)
    run_poll_and_download(args.out_dir, cfg, target_page_id=args.page_id)


if __name__ == "__main__":
    main()
