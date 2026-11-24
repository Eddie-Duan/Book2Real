#!/usr/bin/env python3
"""把 scene_plan.json 变成 prompts.json（每个场景一条正式提示词契约）。

契约字段（官方要求的生成输入，缺一不可）：
    scene_id / pages / first_frame / duration_sec / prompt / negative

- first_frame：该场景第一页的清字关键帧（图生视频的首帧）
- prompt：风格 DNA + 该场景正文（按页顺序拼接，去掉换行）+ 无文字约束
- negative：明确禁止项（文字/字幕/水印/多余角色/写实照片）
- duration_sec：场景内所有镜头的时长之和

用法：
    python build_prompts.py --book books/cn01 --scene-plan .tmp/scene_plan_cn01.json \
        --pages-dir .tmp/skill_pages --out .tmp/prompts_cn01.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from camera_language import enrich as enrich_camera  # noqa: E402

DEFAULT_STYLE_DNA = (
    "儿童绘本插画风格；严格保持原书的画风、笔触、配色与光线；"
    "构图与主体比例沿用参考图；同一场景内只有一个固定机位，"
    "全程不推近、不拉远、不摇、不移、不变焦，主体尺寸与位置保持不变。"
)
DEFAULT_NEGATIVE = (
    "画面中出现文字, 字幕, 音效字, 水印, logo, 台标, 边框, 多余人物, "
    "现代物品, 写实照片, 文字水印, 变形, 扭曲, 闪烁"
)
PROMPT_CHAR_LIMIT = 1000


def _shot_bounds(seg: dict, tr: dict) -> list[str]:
    """Opening/closing rules that defer to the frames the generator is given.

    A designed transition is realised by a shared bridge frame (the previous
    shot must END on it, the next shot must START on it), so the prompt must
    lock onto the anchors instead of inventing a fade.
    """
    out: list[str] = []
    if seg.get("first_frame"):
        out.append("镜头开场：严格从给定的首帧参考图开始，不淡入、不改构图。")
    last = seg.get("last_frame") or {}
    if last.get("source"):
        if (last.get("bridge_kind") or "") == "foreground_occlude":
            out.append("镜头收尾：末帧必须与给定的尾帧桥接图一致——前景遮挡物盖满全屏；"
                       "不淡出、不切机位。")
        else:
            out.append("镜头收尾：末帧必须与给定的尾帧参考图一致；不淡出、不切机位。")
        return out
    decision = str(tr.get("decision") or tr.get("type") or "").upper()
    subtype = str(tr.get("subtype") or "").upper()
    if decision == "END_OF_BOOK":
        out.append("镜头收尾：自然停住，不要淡出。")
    elif decision == "MATCH_CUT" or subtype == "GRAPHIC_MATCH":
        out.append("镜头收尾：保持构图稳定，末帧与下一镜首帧呼应；不要淡出。")
    elif str(tr.get("realization") or "").upper() == "POST":
        out.append("镜头收尾：画面稳定到结束，不要自行淡出（叠化在后期加）。")
    else:
        out.append("镜头收尾：画面稳定到结束，不要自行淡出、不切机位"
                   "（转场在拼接阶段处理）。")
    return out


def load_text_by_page(book: dict) -> dict:
    return {p.get("id"): (p.get("text") or "").strip() for p in (book.get("pages") or [])}


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit()).lstrip("0") or "0"


def resolve_page_dir(pages_dir: Path, pid: str) -> Path | None:
    """把 book 页 id（p01）映射到 S1 目录（page_001）。

    顺序：目录名相同 → page_manifest.json 的 page_id 相同 → 序号相同（p01 ↔ page_001）。
    """
    direct = Path(pages_dir) / str(pid)
    if (direct / "clean_keyframe.png").is_file():
        return direct
    for sub in sorted(Path(pages_dir).iterdir()) if Path(pages_dir).is_dir() else []:
        if not sub.is_dir():
            continue
        mf = sub / "page_manifest.json"
        if mf.is_file():
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if str(data.get("page_id") or "") == str(pid):
                return sub
    target = _digits(pid)
    for sub in sorted(Path(pages_dir).iterdir()) if Path(pages_dir).is_dir() else []:
        if sub.is_dir() and (sub / "clean_keyframe.png").is_file() and _digits(sub.name) == target:
            return sub
    return None


def _has_characters(book: dict, pages: list[str]) -> bool:
    """该场景是否有角色：兼容 book 页 characters（dict/name）与 p01/page_001 两种 id。"""
    wanted = {_digits(p) for p in pages}
    for p in book.get("pages") or []:
        if _digits(p.get("id")) not in wanted:
            continue
        chars = p.get("characters") or []
        if chars:
            return True
    return False


def _character_anchors(book_dir: Path, pages: list[str]) -> list[str]:
    """Identities that must survive the shot, taken from the reader's cast file."""
    p = Path(book_dir) / "reader" / "characters" / "characters.json"
    if not p.is_file():
        return []
    try:
        chars = json.loads(p.read_text(encoding="utf-8")).get("characters") or []
    except Exception:
        return []
    out: list[str] = []
    wanted = {_digits(p) for p in pages}
    for c in chars:
        cps = {_digits(p) for p in (c.get("pages") or [])}
        if cps and not (cps & wanted):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        role = str(c.get("role") or "").strip()
        desc = str(c.get("desc") or "").strip().rstrip("。.")[:44]
        out.append(f"{name}（{role}）{desc}".strip())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="scene_plan -> prompts.json")
    ap.add_argument("--book", required=True)
    ap.add_argument("--scene-plan", required=True)
    ap.add_argument("--pages-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--style-dna", default=DEFAULT_STYLE_DNA)
    ap.add_argument("--negative", default=DEFAULT_NEGATIVE)
    a = ap.parse_args()

    book_path = Path(a.book) / "reader" / "book.json"
    if not book_path.is_file():
        book_path = Path(a.book) / "book.json"
    book = json.loads(book_path.read_text(encoding="utf-8"))
    texts = load_text_by_page(book)
    plan = json.loads(Path(a.scene_plan).read_text(encoding="utf-8"))
    pages_dir = Path(a.pages_dir)

    rows = []
    problems = []
    transitions = {s.get("segment_id"): (s.get("transition_out") or {})
                   for s in plan.get("segments") or []}
    prev_tr: dict = {}
    for seg in plan.get("segments") or []:
        sid = seg.get("segment_id")
        pages = seg.get("pages") or []
        if not pages:
            problems.append(f"{sid} 没有页")
            continue
        pdir = resolve_page_dir(pages_dir, pages[0])
        kf = (pdir / "clean_keyframe.png") if pdir else (pages_dir / pages[0] / "clean_keyframe.png")
        if not pdir or not kf.is_file():
            problems.append(f"{sid} 缺首帧 {pages[0]}（pages-dir 里找不到对应 page 目录）")
            continue
        body = " ".join(texts.get(pid, "") for pid in pages).replace("\n", " ").strip()
        if not body:
            problems.append(f"{sid} 正文为空，提示词缺内容")
        tr = transitions.get(sid) or {}
        parts = [a.style_dna, f"本场景画面：{body}",
                 "画面本身不要出现任何文字；保持角色一致。",
                 "机位锁定：不推近、不拉远、不摇、不移、不变焦；"
                 "人物动作完整可见、按正常速度完成。"]
        parts.extend(_shot_bounds(seg, tr))
        anchors = _character_anchors(Path(a.book), pages)
        if anchors:
            parts.append("角色锚点：" + "；".join(anchors)
                         + "。整段保持同一发型、服饰、比例与配色，不得换人或改龄。")
        prompt = "\n".join(parts)[:PROMPT_CHAR_LIMIT]
        prev_tr = tr
        shots = seg.get("shot_plan") or []
        dur = sum(int(s.get("duration_sec") or 0) for s in shots) or int(seg.get("duration_sec") or 0)
        rows.append({
            "scene_id": sid,
            "pages": pages,
            "first_frame": str(kf),
            "duration_sec": dur,
            "shots": [{"kind": s.get("kind"), "pages": s.get("pages"),
                       "duration_sec": s.get("duration_sec")} for s in shots],
            "transition_out": tr,
            "action": body[:120],
            "prompt": prompt,
            "negative": a.negative,
        })

    scene_chars = {r["scene_id"]: (r["pages"] if _has_characters(book, r["pages"]) else [])
                   for r in rows}

    out = {
        "book_id": book.get("book_id") or Path(a.book).name,
        "lang": (book.get("language") or "zh").lower(),
        "style_dna": a.style_dna,
        "scene_count": len(rows),
        "total_sec": sum(r["duration_sec"] for r in rows),
        "segments": rows,
        "problems": problems,
    }
    out = enrich_camera(out, scene_chars)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[prompts] {out['scene_count']} 场 / {out['total_sec']}s -> {a.out}")
    for r in rows:
        print(f"  {r['scene_id']}  {r['duration_sec']:>2}s  {'+'.join(r['pages'])}  "
              f"prompt {len(r['prompt'])} 字")
    for p in problems:
        print(f"  [warn] {p}")
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())