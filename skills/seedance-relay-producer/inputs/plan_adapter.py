#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan_adapter.py — 把 scene_plan.json 的 segments[] 翻译成 relay 的“页记录”。

为什么需要它
------------
relay 原有入口是 `pages/page_###/`，一个目录一条片；而“整本一次生成、不可二次编辑”
的产品模型下，最小可替换单位是导演切出来的**段**（可合并多页、可带桥接帧 T），
不是页。两者形状不同，但 relay 下游（generate / poll_download / qc / concat）只认
`pipeline_state.json` 里的 page 记录，所以只要把“段”伪装成“页”，下游一行都不用改。

映射规则（与 references/plan-adapter.md 一致）
------------------------------------------------
  page_id            <- segment_id
  page_index         <- 段的先后顺序
  keyframe_path      <- 段 first_frame（keyframe -> pages/page_###/clean_keyframe.png
                                          bridge_frame -> <plan_dir>/bridge/T_*.png）
  last_frame_path    <- **下一段的 first_frame**（不是本段 last_frame）
  其余锚点缺失       -> 阻断（宁可停下，也不悄悄降级成硬切）
  prompt_path        <- <plan_dir>/prompts/seg_###_prompt.txt
  duration_sec       <- 段时长（5 / 10）
  relay_level        <- 恒 "A"：转场方式已由导演决定，不在这里重判 A/B/C
  transition_after   <- concat_plan：cut=hard -> None；cut=xfade -> "fade"
  upstream_qa        <- 段内各页 qa_report 取最差（verdict 必须全 PASS）

本模块只依赖 numpy/Pillow 之外的纯标准库 + Pillow，可在无 cv2 的环境里单测。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

PLAN_STATUS = "SCENE_PLAN_READY"
ALLOWED_DURATIONS = (5, 10)


class PlanContractError(RuntimeError):
    """计划本身不可用（结构缺失 / 锚点不存在），由 preflight 统一转成阻断。"""

    def __init__(self, problems: List[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise PlanContractError([f"文件不存在: {path}"]) from exc
    except json.JSONDecodeError as exc:
        raise PlanContractError([f"{path} 不是合法 JSON: {exc}"]) from exc


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _image_size(path: Path) -> Tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def _ratio_str(width: int, height: int) -> str:
    from math import gcd

    g = gcd(width, height) or 1
    return f"{width // g}:{height // g}"


def resolve_anchor(
    anchor: Optional[Dict[str, Any]],
    pages_dir: Path,
    plan_dir: Path,
) -> Tuple[Optional[Path], Optional[str]]:
    """把 scene_plan 的 first_frame/last_frame 描述解析成真实文件路径。

    返回 (path, error)。path 为 None 表示该槽位本来就没有（末段的 last_frame）。
    """
    if not anchor:
        return None, None
    kind = anchor.get("kind")
    source = anchor.get("source")
    if not source:
        return None, f"锚点缺少 source: {anchor}"
    if kind == "keyframe":
        path = (pages_dir / source).resolve()
    elif kind == "bridge_frame":
        path = (plan_dir / source).resolve()
    else:
        return None, f"未知锚点类型 kind={kind!r}（只支持 keyframe / bridge_frame）"
    if not path.is_file():
        return None, f"锚点文件不存在: {path}"
    return path, None


def read_page_meta(pages_dir: Path, page_id: str) -> Dict[str, Any]:
    """读单页 manifest（宽松：缺文件只记 warning，字段缺失留空）。"""
    pdir = pages_dir / page_id
    meta: Dict[str, Any] = {"page_id": page_id, "page_dir": str(pdir), "exists": pdir.is_dir()}
    manifest_path = pdir / "page_manifest.json"
    if manifest_path.is_file():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            meta["manifest_error"] = "page_manifest.json 解析失败"
        else:
            meta["scene_tag"] = raw.get("scene_tag")
            meta["character_ids"] = raw.get("character_ids") or []
            meta["time_tag"] = raw.get("time_tag")
    return meta


def read_page_qa(pages_dir: Path, page_id: str) -> Dict[str, Any]:
    """读单页 qa_report，返回扁平化后的关键指标（缺键记为 None）。"""
    qpath = pages_dir / page_id / "qa_report.json"
    out: Dict[str, Any] = {
        "page_id": page_id,
        "verdict": None,
        "ocr_residual": None,
        "non_edited_ssim": None,
        "protected_intrusion": None,
    }
    if not qpath.is_file():
        return out
    try:
        raw = json.loads(qpath.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return out
    eff = {**raw, **(raw.get("metrics") or {})}
    out["verdict"] = eff.get("qa_verdict", eff.get("verdict", eff.get("status")))
    if eff.get("ocr_residual_count") is not None:
        out["ocr_residual"] = int(eff["ocr_residual_count"])
    ssim = eff.get("non_edited_ssim", eff.get("ssim"))
    if ssim is not None:
        out["non_edited_ssim"] = float(ssim)
    intr = eff.get("protected_zone_intrusion_px", eff.get("intrusion_px"))
    if intr is not None:
        out["protected_intrusion"] = int(intr)
    return out


def aggregate_upstream_qa(
    pages_dir: Path,
    page_ids: List[str],
    thresholds: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """段内各页 QA 取最差。返回 (upstream_qa, block_reasons, warnings)。

    缺指标不算作弊：只在**有值**时判断门禁，缺的记 warning，让上层知道这份 QA 不完整。
    """
    blocks: List[str] = []
    warnings: List[str] = []
    rows = [read_page_qa(pages_dir, pid) for pid in page_ids]

    for row in rows:
        verdict = row["verdict"]
        if verdict is None:
            warnings.append(f"{row['page_id']}: qa_report 缺 verdict")
        elif str(verdict).upper() != "PASS":
            blocks.append(f"上游页 {row['page_id']} qa 判定未通过: verdict={verdict}")

    def worst(key: str, pick, default):
        vals = [r[key] for r in rows if r[key] is not None]
        return pick(vals) if vals else default

    ocr = worst("ocr_residual", max, 0)
    ssim = worst("non_edited_ssim", min, 1.0)
    intr = worst("protected_intrusion", max, 0)

    if not any(r["ocr_residual"] is not None for r in rows):
        warnings.append("段内各页均缺 ocr_residual_count，白字残留门禁未验证")
    elif ocr > 0:
        blocks.append(f"上游仍有文字残留: ocr_residual={ocr}")

    if not any(r["non_edited_ssim"] is not None for r in rows):
        warnings.append("段内各页均缺 non_edited_ssim，非编辑区一致性未验证")
    else:
        min_ssim = float(thresholds.get("upstream_min_ssim", 0.9990))
        if ssim < min_ssim:
            blocks.append(f"上游非编辑区 SSIM 不达标: {ssim} < {min_ssim}")

    if not any(r["protected_intrusion"] is not None for r in rows):
        warnings.append("段内各页均缺 protected_zone_intrusion_px，保护带未验证")
    else:
        max_intr = int(thresholds.get("upstream_max_intrusion_px", 0))
        if intr > max_intr:
            blocks.append(f"上游保护带入侵: {intr}px > {max_intr}px")

    upstream_qa = {
        "verdict": "PASS" if not any(
            r["verdict"] is not None and str(r["verdict"]).upper() != "PASS" for r in rows
        ) else "FAIL",
        "resolution_delta": [0, 0],
        "ocr_residual": ocr,
        "non_edited_ssim": ssim,
        "protected_intrusion": intr,
        "_provenance": "SCENE_PLAN",
        "_pages": page_ids,
    }
    return upstream_qa, blocks, warnings


def _transition_for(concat_plan: List[Dict[str, Any]], seg_id: str) -> Tuple[Optional[str], float]:
    for entry in concat_plan:
        if entry.get("after_segment") == seg_id:
            cut = (entry.get("cut") or "hard").lower()
            sec = float(entry.get("xfade_sec") or 0.0)
            if cut == "xfade":
                return "fade", sec
            return None, 0.0
    return None, 0.0


def build_plan_records(
    plan_path: Path,
    pages_dir: Path,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """把 scene_plan.json 翻译成 relay 的 page 记录列表（含 generation_plan）。"""
    plan = _load_json(plan_path)
    plan_dir = plan_path.resolve().parent
    segments: List[Dict[str, Any]] = plan.get("segments") or []
    if not segments:
        raise PlanContractError([f"{plan_path} 中没有任何 segments，无法生成"])

    master_ratio = plan.get("master_ratio") or ""
    concat_plan = plan.get("concat_plan") or []
    thresholds = cfg["thresholds"]
    max_prompt_len = int(cfg["ark"]["max_prompt_length"])

    from scripts.common import find_closest_aspect_ratio  # 延迟导入，避免纯校验场景拉起 cv2

    records: List[Dict[str, Any]] = []
    warnings: List[str] = []

    # 先解析所有段的锚点，尾帧要用到“下一段的首帧”
    resolved: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        seg_id = seg.get("segment_id")
        if not seg_id:
            raise PlanContractError([f"第 {idx + 1} 段缺少 segment_id"])
        first_path, first_err = resolve_anchor(seg.get("first_frame"), pages_dir, plan_dir)
        own_last, own_last_err = resolve_anchor(seg.get("last_frame"), pages_dir, plan_dir)
        resolved.append(
            {
                "seg": seg,
                "seg_id": seg_id,
                "first_path": first_path,
                "first_err": first_err,
                "own_last": own_last,
                "own_last_err": own_last_err,
            }
        )

    for idx, item in enumerate(resolved):
        seg = item["seg"]
        seg_id = item["seg_id"]
        blocks: List[str] = []

        first_path = item["first_path"]
        if item["first_err"]:
            blocks.append(f"首帧锚点不可用: {item['first_err']}")

        # 尾帧：用本段计划里的 last_frame。
        # 注意 SHARED_T 接缝下，本段 last_frame 必须与下一段 first_frame 是同一张 T，
        # 否则两条片拼起来会在接缝处跳一下——这是计划自身的错，直接阻断。
        next_item = resolved[idx + 1] if idx + 1 < len(resolved) else None
        last_path = item["own_last"]
        if item["own_last_err"]:
            blocks.append(f"尾帧锚点不可用: {item['own_last_err']}")

        realization = ((seg.get("transition_out") or {}).get("realization") or "").upper()
        if realization == "SHARED_T":
            if next_item is None:
                blocks.append("标了 SHARED_T 但已是最后一段，计划自相矛盾")
            elif last_path is None or next_item["first_path"] is None:
                blocks.append("标了 SHARED_T 但共享桥接帧缺失")
            elif last_path.resolve() != next_item["first_path"].resolve():
                blocks.append(
                    f"标了 SHARED_T，但本段尾帧 ({item['own_last']}) 与下一段首帧 "
                    f"({next_item['first_path']}) 不是同一张"
                )

        duration = int(seg.get("duration_sec") or 5)
        if duration not in ALLOWED_DURATIONS:
            blocks.append(f"段时长 {duration}s 不在允许集合 {ALLOWED_DURATIONS}")

        page_ids = list(seg.get("pages") or [])
        if not page_ids:
            blocks.append("段内没有 pages")

        prompt_path = (plan_dir / str(seg.get("prompt_file") or "")).resolve() if seg.get("prompt_file") else None
        prompt_meta: Dict[str, Any] = {"valid": False, "reason": "未提供 prompt_file", "warnings": []}
        if prompt_path is not None:
            if not prompt_path.is_file():
                blocks.append(f"段提示词不存在: {prompt_path}")
            else:
                text = prompt_path.read_text(encoding="utf-8")
                from scripts.preflight import parse_prompt_metadata  # 复用同一份定义

                prompt_meta = parse_prompt_metadata(text, max_prompt_len)
                if not prompt_meta["valid"]:
                    blocks.append(f"提示词校验失败: {prompt_meta['reason']}")

        width, height = 0, 0
        if first_path is not None and first_path.is_file():
            try:
                width, height = _image_size(first_path)
            except Exception as exc:  # noqa: BLE001
                blocks.append(f"首帧无法解码: {exc}")

        if last_path is not None and first_path is not None and first_path.is_file() and last_path.is_file():
            try:
                lw, lh = _image_size(last_path)
                if (lw, lh) != (width, height):
                    blocks.append(
                        f"段首帧 {width}x{height} 与尾帧 {lw}x{lh} 尺寸不一致，双锚定会形变"
                    )
            except Exception as exc:  # noqa: BLE001
                blocks.append(f"尾帧无法解码: {exc}")

        upstream_qa, qa_blocks, qa_warnings = aggregate_upstream_qa(pages_dir, page_ids, thresholds)
        blocks.extend(qa_blocks)
        warnings.extend(qa_warnings)

        metas = [read_page_meta(pages_dir, pid) for pid in page_ids]
        scene_tags = {m.get("scene_tag") for m in metas if m.get("scene_tag")}
        characters: List[str] = []
        for m in metas:
            for c in m.get("character_ids") or []:
                if c not in characters:
                    characters.append(c)

        target_ratio, ratio_diff = find_closest_aspect_ratio(width, height, cfg["ark"]["allowed_ratios"]) if width else (master_ratio, 0.0)

        transition_after, transition_sec = _transition_for(concat_plan, seg_id)

        record: Dict[str, Any] = {
            "page_dir": str(plan_dir),
            "page_id": seg_id,
            "segment_id": seg_id,
            "keyframe_path": str(first_path) if first_path else "",
            "prompt_path": str(prompt_path) if prompt_path else "",
            "manifest_status": PLAN_STATUS,
            "width": width,
            "height": height,
            "aspect_ratio": _ratio_str(width, height) if width else master_ratio,
            "scene_tag": (sorted(scene_tags)[0] if scene_tags else None),
            "character_ids": characters,
            "completion_items": [],
            "upstream_qa": upstream_qa,
            "raw_manifest": {"plan_version": plan.get("plan_version"), "segment_id": seg_id},
            "raw_qa": {},
            "page_index": idx,
            "status": "BLOCKED" if blocks else "PREFLIGHT_READY",
            "block_reasons": blocks,
            "prompt_meta": prompt_meta,
            "target_ratio": target_ratio,
            "ratio_diff": round(float(ratio_diff or 0.0), 5),
            "segment": {
                "segment_id": seg_id,
                "pages": page_ids,
                "segment_kind": seg.get("segment_kind"),
                "duration_sec": duration,
                "first_frame_source": (seg.get("first_frame") or {}).get("source"),
                "last_frame_source": (seg.get("last_frame") or {}).get("source"),
            },
            "generation_plan": {
                "first_frame_source": (seg.get("first_frame") or {}).get("source"),
                "last_frame_source": (seg.get("last_frame") or {}).get("source"),
                "last_frame_path": str(last_path) if last_path else None,
                "relay_level": "A" if last_path else "INDEPENDENT",
                "duration_sec": duration,
                "is_terminal_page": idx == len(resolved) - 1,
                "needs_smooth_lock": False,
                "needs_ending_lock": idx == len(resolved) - 1,
                "transition_after": transition_after,
                "transition_sec": transition_sec,
            },
        }
        records.append(record)

    return {
        "records": records,
        "master_ratio": master_ratio,
        "plan_version": plan.get("plan_version"),
        "plan_path": str(plan_path.resolve()),
        "plan_dir": str(plan_dir),
        "pages_dir": str(pages_dir.resolve()),
        "warnings": warnings,
    }
