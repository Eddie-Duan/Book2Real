#!/usr/bin/env python3
"""
集中式上游输入适配器 (Fail-Closed 契约校验 + --probe 字段探测)。
严格禁止猜测未知字段；任何未在映射表声明的键或缺失必填键立即阻断。
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

# 权威字段映射表（同步记录于 references/preflight-and-adapter.md）
MANIFEST_KEY_MAP: Dict[str, List[str]] = {
    "page_id": ["page_id", "id"],
    "status": ["pipeline_status", "status"],
    "resolution": ["asset_info.locked_resolution", "locked_resolution", "resolution"],
    "aspect_ratio": ["asset_info.locked_aspect_ratio", "locked_aspect_ratio", "aspect_ratio"],
    "language": ["asset_info.detected_language", "detected_language"],
    "text_blocks": ["text_blocks"],
    "completion_items": ["completion_items"],
    "scene_tag": ["scene_tag", "scene_context"],
    "character_ids": ["character_ids", "characters"],
}

MANIFEST_ALLOWED_TOP_KEYS: Set[str] = {
    "manifest_version",
    "pipeline_status",
    "status",
    "multi_page_mode",
    "human_review_required",
    "asset_info",
    "stage_artifacts",
    "text_blocks",
    "style_dna",
    "completion_items",
    "qa_metrics",
    "seedance_output",
    "scene_tag",
    "scene_context",
    "character_ids",
    "characters",
    "time_tag",
    "page_id",
    "id",
    "locked_resolution",
    "resolution",
    "locked_aspect_ratio",
    "aspect_ratio",
}

QA_KEY_MAP: Dict[str, List[str]] = {
    "verdict": ["qa_verdict", "verdict", "status"],
    "resolution_delta": ["resolution_delta_px"],
    "ocr_residual": ["ocr_residual_count"],
    "non_edited_ssim": ["non_edited_ssim", "ssim"],
    "protected_intrusion": ["protected_zone_intrusion_px", "intrusion_px"],
}

QA_ALLOWED_TOP_KEYS: Set[str] = {
    "qa_verdict",
    "verdict",
    "status",
    "resolution_delta_px",
    "ocr_residual_count",
    "art_text_edge_density_diff_pct",
    "non_edited_ssim",
    "ssim",
    "protected_zone_intrusion_px",
    "intrusion_px",
    "style_histogram_bhattacharyya",
    "laplacian_var_ratio",
    "metrics",
    "page_id",
}


class ContractSchemaError(RuntimeError):
    """Fail-closed 契约违例异常。"""


def _extract_nested(data: Dict[str, Any], dotted_path: str) -> Any:
    cur: Any = data
    for part in dotted_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _resolve_mapped_field(
    data: Dict[str, Any], candidates: List[str], field_label: str, required: bool = True
) -> Any:
    for cand in candidates:
        val = _extract_nested(data, cand)
        if val is not None:
            return val
    if required:
        raise ContractSchemaError(
            f"[FAIL-CLOSED] 缺失必填映射字段 '{field_label}'，候选路径 {candidates} 均未命中。"
        )
    return None


def validate_no_unknown_keys(
    data: Dict[str, Any], allowed_keys: Set[str], source_name: str
) -> None:
    unknown = set(data.keys()) - allowed_keys
    if unknown:
        raise ContractSchemaError(
            f"[FAIL-CLOSED] {source_name} 包含未注册的未知顶层键: {sorted(unknown)}。"
            f"请先核对契约并在 inputs/adapter.py 与 references/preflight-and-adapter.md 注册后再运行。"
        )


def _load_json(fpath: Path) -> Dict[str, Any]:
    """以 utf-8-sig 容错读取 JSON（自动剥离 Windows 工具写入的 BOM），解析失败转 fail-closed。"""
    try:
        return json.loads(fpath.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ContractSchemaError(
            f"[FAIL-CLOSED] {fpath} 不是合法 JSON: {exc}"
        ) from exc


def load_and_adapt_page(page_dir: Path) -> Dict[str, Any]:
    """加载单页交付目录，严格校验文件存在性与 JSON 契约，输出标准化内部记录。"""
    required_files = {
        "keyframe": page_dir / "clean_keyframe.png",
        "manifest": page_dir / "page_manifest.json",
        "qa_report": page_dir / "qa_report.json",
        "prompt": page_dir / "seedance_prompt.txt",
    }
    for name, fpath in required_files.items():
        if not fpath.is_file():
            raise ContractSchemaError(
                f"[FAIL-CLOSED] 页面目录 {page_dir} 缺失必需输入文件: {fpath.name} ({name})"
            )

    manifest_raw = _load_json(required_files["manifest"])
    qa_raw = _load_json(required_files["qa_report"])

    # 支持 qa_report 嵌套在 metrics 下的情况，将其展平校验顶层键
    validate_no_unknown_keys(manifest_raw, MANIFEST_ALLOWED_TOP_KEYS, f"{page_dir.name}/page_manifest.json")
    validate_no_unknown_keys(qa_raw, QA_ALLOWED_TOP_KEYS, f"{page_dir.name}/qa_report.json")
    qa_effective = {**qa_raw, **(qa_raw.get("metrics") or {})}

    res_obj = _resolve_mapped_field(manifest_raw, MANIFEST_KEY_MAP["resolution"], "resolution", required=True)
    if not isinstance(res_obj, dict) or "width" not in res_obj or "height" not in res_obj:
        raise ContractSchemaError(f"[FAIL-CLOSED] {page_dir.name} resolution 格式非法: {res_obj}")

    adapted = {
        "page_dir": str(page_dir.resolve()),
        "page_id": _resolve_mapped_field(manifest_raw, MANIFEST_KEY_MAP["page_id"], "page_id", required=False)
        or page_dir.name,
        "keyframe_path": str(required_files["keyframe"].resolve()),
        "prompt_path": str(required_files["prompt"].resolve()),
        "manifest_status": _resolve_mapped_field(manifest_raw, MANIFEST_KEY_MAP["status"], "status", required=True),
        "width": int(res_obj["width"]),
        "height": int(res_obj["height"]),
        "aspect_ratio": _resolve_mapped_field(
            manifest_raw, MANIFEST_KEY_MAP["aspect_ratio"], "aspect_ratio", required=False
        ),
        "scene_tag": _resolve_mapped_field(manifest_raw, MANIFEST_KEY_MAP["scene_tag"], "scene_tag", required=False),
        "character_ids": _resolve_mapped_field(
            manifest_raw, MANIFEST_KEY_MAP["character_ids"], "character_ids", required=False
        )
        or [],
        "completion_items": _resolve_mapped_field(
            manifest_raw, MANIFEST_KEY_MAP["completion_items"], "completion_items", required=False
        )
        or [],
        "upstream_qa": {
            "verdict": _resolve_mapped_field(qa_effective, QA_KEY_MAP["verdict"], "qa.verdict", required=True),
            "resolution_delta": _resolve_mapped_field(
                qa_effective, QA_KEY_MAP["resolution_delta"], "qa.resolution_delta", required=False
            )
            or [0, 0],
            "ocr_residual": int(
                _resolve_mapped_field(qa_effective, QA_KEY_MAP["ocr_residual"], "qa.ocr_residual", required=True)
            ),
            "non_edited_ssim": float(
                _resolve_mapped_field(
                    qa_effective, QA_KEY_MAP["non_edited_ssim"], "qa.non_edited_ssim", required=True
                )
            ),
            "protected_intrusion": int(
                _resolve_mapped_field(
                    qa_effective, QA_KEY_MAP["protected_intrusion"], "qa.protected_intrusion", required=True
                )
            ),
        },
        "raw_manifest": manifest_raw,
        "raw_qa": qa_raw,
    }
    return adapted


def probe_pages_directory(pages_dir: Path) -> Dict[str, Any]:
    """扫描所有页面目录，打印检测到的实际字段结构，辅助契约核验。"""
    page_dirs = sorted([p for p in pages_dir.iterdir() if p.is_dir()])
    summary: Dict[str, Any] = {"pages_dir": str(pages_dir), "detected_pages": []}
    for pdir in page_dirs:
        entry: Dict[str, Any] = {"dir": pdir.name, "files": [f.name for f in pdir.iterdir()]}
        mfile = pdir / "page_manifest.json"
        qfile = pdir / "qa_report.json"
        if mfile.is_file():
            try:
                mdata = _load_json(mfile)
            except ContractSchemaError as exc:
                entry["manifest_parse_error"] = str(exc)
            else:
                entry["manifest_top_keys"] = sorted(list(mdata.keys()))
                if isinstance(mdata.get("asset_info"), dict):
                    entry["manifest_asset_info_keys"] = sorted(list(mdata["asset_info"].keys()))
        if qfile.is_file():
            try:
                qdata = _load_json(qfile)
            except ContractSchemaError as exc:
                entry["qa_parse_error"] = str(exc)
            else:
                entry["qa_top_keys"] = sorted(list(qdata.keys()))
        summary["detected_pages"].append(entry)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Upstream picturebook artifact schema probe & validator")
    parser.add_argument("--pages-dir", type=Path, required=True, help="Root directory containing page_### folders")
    parser.add_argument("--probe", action="store_true", help="Print detected keys in JSON files without failing")
    parser.add_argument("--dry-run", action="store_true", help="Validate schema strictly without side effects")
    args = parser.parse_args()

    if not args.pages_dir.is_dir():
        print(f"ERROR: Directory not found: {args.pages_dir}", file=sys.stderr)
        sys.exit(2)

    if args.probe:
        report = probe_pages_directory(args.pages_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(0)

    page_dirs = sorted([p for p in args.pages_dir.iterdir() if p.is_dir()])
    try:
        for pdir in page_dirs:
            adapted = load_and_adapt_page(pdir)
            print(f"[OK] {pdir.name}: {adapted['width']}x{adapted['height']}, QA={adapted['upstream_qa']['verdict']}")
    except ContractSchemaError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
