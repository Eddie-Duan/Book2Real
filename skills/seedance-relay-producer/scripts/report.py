#!/usr/bin/env python3
"""
Step 6B: 生成机读 qc_report.json 与人读 qc_report.md 汇总交付报告。
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.common import load_pipeline_state, utc_now_iso


def generate_reports(out_dir: Path) -> Dict[str, Any]:
    state = load_pipeline_state(out_dir)
    pages = sorted(state["pages"].values(), key=lambda x: x.get("preflight", {}).get("page_index", 0))

    counts = {"PASS": 0, "BLOCKED": 0, "ESCALATE": 0, "OTHER": 0}
    total_cost = 0.0
    page_summaries: List[Dict[str, Any]] = []

    for p in pages:
        st = p["status"]
        if st in counts:
            counts[st] += 1
        else:
            counts["OTHER"] += 1

        page_cost = sum(float(h.get("estimated_cost", 0.0)) for h in p.get("history", []))
        total_cost += page_cost

        pf = p.get("preflight", {})
        plan = pf.get("generation_plan", {})
        lqc = p.get("latest_qc") or {}

        page_summaries.append(
            {
                "page_id": p["page_id"],
                "status": st,
                "attempts": p.get("attempts", 0),
                "relay_level": plan.get("relay_level", "N/A"),
                "first_frame": plan.get("first_frame_source", "N/A"),
                "last_frame": plan.get("last_frame_source", "None"),
                "duration_sec": plan.get("duration_sec", "N/A"),
                "cost_spent": round(page_cost, 4),
                "block_reasons": pf.get("block_reasons", []),
                "defects": lqc.get("defects", []),
                "metrics": lqc.get("checks", {}),
            }
        )

    report_json = {
        "generated_at": utc_now_iso(),
        "relay_enabled": state.get("meta", {}).get("relay_enabled", False),
        "target_resolution": state.get("meta", {}).get("target_resolution", "unknown"),
        "summary_counts": counts,
        "total_cost_spent": round(total_cost, 4),
        "relay_edges": state.get("relay_edges", []),
        "pages": page_summaries,
        "compliance_notice": "本成片由火山方舟 Seedance AI 视频模型辅助生成，保留原始绘本艺术构图与 AI 生成标识。",
    }

    json_path = out_dir / "qc_report.json"
    json_path.write_text(json.dumps(report_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # 生成人读 Markdown 报告
    md_lines = [
        "# Seedance 绘本视频流水线质检与交付报告",
        "",
        f"- **生成时间**: `{report_json['generated_at']}`",
        f"- **接力模式**: `{'开启 (ON)' if report_json['relay_enabled'] else '关闭 (OFF)'}`",
        f"- **目标分辨率**: `{report_json['target_resolution']}`",
        f"- **页面统计**: PASS = `{counts['PASS']}` | BLOCKED = `{counts['BLOCKED']}` | ESCALATE = `{counts['ESCALATE']}`",
        f"- **累计实际花费**: `{report_json['total_cost_spent']}`",
        "",
        "## 1. 逐页执行与质检明细",
        "",
        "| 页面 | 状态 | 尝试次数 | 接力模式 | 入口帧 -> 出口帧 | 时长 | 花费 | 缺陷/阻断说明 |",
        "| :--- | :--- | :---: | :---: | :--- | :---: | :---: | :--- |",
    ]

    for ps in page_summaries:
        anchor_str = f"{ps['first_frame']} -> {ps['last_frame']}"
        issues = ps["block_reasons"] + [f"[{d['code']}] {d['detail']}" for d in ps["defects"]]
        issue_str = "; ".join(issues) if issues else "全部门禁通过"
        md_lines.append(
            f"| {ps['page_id']} | **{ps['status']}** | {ps['attempts']} | {ps['relay_level']} | "
            f"`{anchor_str}` | {ps['duration_sec']}s | {ps['cost_spent']} | {issue_str} |"
        )

    if report_json["relay_edges"]:
        md_lines.extend(
            [
                "",
                "## 2. 相邻页接力降级决策明细",
                "",
                "| 接力边 | 命中级别 | 特征距离 | 判定依据 | 决策说明 |",
                "| :--- | :---: | :---: | :--- | :--- |",
            ]
        )
        for edge in report_json["relay_edges"]:
            # 计划模式（--plan）的接力边没有 DINO 距离：段与段的锚点由导演指定，
            # 这里只记录 level/mode 与理由，不臆造一个"特征距离"。
            dino = edge.get("dino_distance")
            dino_str = f"{dino:.4f}" if isinstance(dino, (int, float)) else "-"
            md_lines.append(
                f"| `{edge['from_page']} -> {edge['to_page']}` | **Level {edge['level']}** ({edge['mode']}) | "
                f"{dino_str} | {edge.get('basis', '-')} | {edge.get('reason', '-')} |"
            )

    md_lines.extend(
        [
            "",
            "## 3. 合规声明",
            f"> {report_json['compliance_notice']}",
        ]
    )

    md_path = out_dir / "qc_report.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"[REPORT] 报告已生成:\n  - {json_path}\n  - {md_path}")
    return report_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate machine-readable and human-readable QC reports")
    parser.add_argument("--out-dir", type=Path, default=Path("out"), help="Workspace output directory")
    args = parser.parse_args()
    generate_reports(args.out_dir)


if __name__ == "__main__":
    main()
