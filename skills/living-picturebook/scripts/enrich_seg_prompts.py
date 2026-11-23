#!/usr/bin/env python3
"""把分镜段落的提示词升级成 relay 要的五段式（镜头语言 + 角色锚点 + 负向约束）。

为什么单独一步：`plan_scenes.py`（场景导演）写出的 `prompts/seg_###_prompt.txt` 只有
上游 `seedance_prompt.txt` + 一句通用 lock，实测 `preflight` 会报缺
`[Camera] / [Subject Motion] / [Environment] / [Negative Constraints]`，而且
`character_ids` 为空时整段没有任何"不许换人"的约束——Seedance 生成时人物会漂。

本脚本只重写 `prompts/seg_###_prompt.txt`（原文件备份为 `.base`），不改 scene_plan，
不改上游 `seedance_prompt.txt`（那是只读原稿）。

用法：
    python enrich_seg_prompts.py --plan .tmp/skill_out/scene_plan.json \
        --pages-dir .tmp/skill_pages --out-dir .tmp/skill_out [--book books/cn01]
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PROMPT_CHAR_LIMIT = 950  # 给 generate.py 追加的 --resolution/--duration/--seed 后缀留位置

DEFAULT_STYLE_DNA = (
    "儿童绘本插画风格；严格保持原书的画风、笔触、配色与光线；"
    "构图、主体比例与背景布局沿用首帧参考图。"
)
DEFAULT_NEGATIVE = (
    "文字, 字幕, 音效字, 手写字, 水印, logo, 台标, 边框, 多余人物, 多余动物, "
    "现代物品, 写实照片, 3D 渲染感, 变形, 扭曲, 闪烁, 缺肢"
)

# 机位政策（2026-11-24，见 docs/CONSTRAINTS.md）：严格静态锁定。
# 与 seedance-relay-producer/scripts/qc.py 的 zoom_check（尺度偏差 >4% 判 ZOOM_DRIFT）一致，
# 也与 picturebook-scene-director 末尾的 [Camera Lock] 同义——不许出现推/拉/摇/移。
CAMERA_LOCK = (
    "[Camera Lock]: locked-off static camera. No zoom in/out, no dolly, no push-in or "
    "pull-out, no pan, no tilt, no framing change; every character and prop keeps a "
    "constant on-screen size and position. Only subtle in-place motion is allowed "
    "(breathing, blink, cloth, hair, light)."
)
STATIC_CAMERA = ("镜头：固定机位、平视、静态构图；机位与取景全程不变，"
                 "人物与道具在屏幕上的尺寸和位置恒定，不做剪辑。")


def camera_line(n_shots: int, scene_no: int, has_chars: bool) -> str:
    return STATIC_CAMERA


def load_book(book_arg: str | None, pages_dir: Path, pages: list[str]) -> tuple[dict, dict]:
    """返回 (book.json, characters.json)。--book 没给就用页 manifest 里的 book_id 反查。"""
    book_dir: Path | None = None
    if book_arg:
        book_dir = Path(book_arg)
    else:
        for pid in pages:
            mf = pages_dir / pid / "page_manifest.json"
            if not mf.is_file():
                continue
            try:
                bid = json.loads(mf.read_text(encoding="utf-8")).get("book_id")
            except Exception:
                bid = None
            if bid:
                cand = REPO / "books" / str(bid)
                if cand.is_dir():
                    book_dir = cand
                break
    if book_dir is None or not book_dir.is_dir():
        return {}, {}
    book: dict = {}
    for rel in (Path("reader") / "book.json", Path("book.json")):
        p = book_dir / rel
        if p.is_file():
            try:
                book = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                book = {}
            break
    chars: dict = {}
    p = book_dir / "reader" / "characters" / "characters.json"
    if p.is_file():
        try:
            chars = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            chars = {}
    return book, chars


def anchors_for(pages: list[str], characters: dict, limit: int = 4) -> list[str]:
    out: list[str] = []
    for c in (characters.get("characters") or []):
        cps = set(c.get("pages") or [])
        if cps and not (cps & set(pages)):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        role = str(c.get("role") or "").strip()
        desc = str(c.get("desc") or "").strip().rstrip("。.")[:36]
        out.append(f"{name}（{role}）{desc}".strip())
        if len(out) >= limit:
            break
    return out


def page_text(pages_dir: Path, pages: list[str]) -> str:
    parts: list[str] = []
    for pid in pages:
        mf = pages_dir / pid / "page_manifest.json"
        if not mf.is_file():
            continue
        try:
            txt = str(json.loads(mf.read_text(encoding="utf-8")).get("text") or "").strip()
        except Exception:
            txt = ""
        if txt:
            parts.append(" ".join(txt.split()))
    return " ".join(parts)[:160]


def build_prompt(seg: dict, scene_no: int, text: str, anchors: list[str], style_dna: str) -> str:
    last = seg.get("last_frame") or {}
    bridge = str(last.get("bridge_kind") or "") == "foreground_occlude"
    sections: list[str] = []

    cam_text = camera_line(1, scene_no, bool(anchors))
    # 摄影机句子末尾带着"人物自然动作/环境微动"，那一段归 [Subject Motion]，避免重复占字数
    for cut in ("；人物自然动作", "；环境微动"):
        if cut in cam_text:
            cam_text = cam_text.split(cut)[0] + "；"
            break
    sections.append(f"[Camera] {cam_text}")
    subject = "人物自然动作：呼吸、眨眼、轻微重心转移；表情克制，不做夸张形变。"
    if not anchors:
        subject = "环境微动：光影、纸纹与前景轻微晃动；主体轮廓保持稳定。"
    if text:
        subject += f"本段动作：{text}"
    sections.append(f"[Subject Motion] {subject}")

    sections.append(f"[Environment & Style] {style_dna}背景与本段首帧一致，"
                    "不随意更换地点、季节、天气与家具摆位。")

    lock = "严格从给定首帧参考图开始，不淡入、不改构图；"
    if last.get("source"):
        lock += ("末帧必须与给定尾帧桥接图一致——前景遮挡物盖满全屏，不淡出、不切机位；"
                 if bridge else "末帧必须与给定尾帧参考图一致，不淡出、不切机位；")
    else:
        lock += "镜头收尾自然停住，不要自行淡出；"
    if anchors:
        lock += ("角色锚点：" + "；".join(anchors)
                 + "。整段保持同一发型、服饰、比例与配色，不得换人、改龄或增减主角。")
    sections.append(f"[Transition/Continuity Lock] {lock}")

    sections.append(f"[Negative Constraints] {DEFAULT_NEGATIVE}")
    # 尾部固定 Camera Lock（picturebook-pipeline 的 S2 门禁要求"逐段 prompt 尾部都有 [Camera Lock]"）
    sections.append(CAMERA_LOCK)

    text_out = "\n".join(sections)
    if len(text_out) > PROMPT_CHAR_LIMIT:
        # 超长先砍形容词最密集的 Subject Motion，再不行砍环境说明
        sections[1] = "[Subject Motion] " + subject.split("本段动作：")[0]
        text_out = "\n".join(sections)
    if len(text_out) > PROMPT_CHAR_LIMIT:
        text_out = text_out[:PROMPT_CHAR_LIMIT]
    return text_out


def main() -> int:
    ap = argparse.ArgumentParser(description="scene_plan -> 五段式 relay 提示词")
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--pages-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--book", default=None, help="书目录；不给就用页 manifest 的 book_id 反查")
    ap.add_argument("--style-dna", default=DEFAULT_STYLE_DNA)
    a = ap.parse_args()

    plan = json.loads(a.plan.read_text(encoding="utf-8"))
    segments = plan.get("segments") or []
    if not segments:
        raise SystemExit(f"[enrich] {a.plan} 里没有 segments")

    all_pages = [p for s in segments for p in (s.get("pages") or [])]
    book, characters = load_book(a.book, a.pages_dir, all_pages)
    scene_ids = [s.get("scene_id") or s.get("segment_id") for s in segments]

    prompts_dir = a.out_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for seg in segments:
        sid = str(seg.get("segment_id"))
        pages = [str(p) for p in (seg.get("pages") or [])]
        scene_key = seg.get("scene_id") or sid
        scene_no = (scene_ids.index(scene_key) if scene_key in scene_ids else 0) + 1
        anchors = anchors_for(pages, characters)
        text = page_text(a.pages_dir, pages)
        prompt = build_prompt(seg, scene_no, text, anchors, a.style_dna)

        target = prompts_dir / f"{sid}_prompt.txt"
        if target.is_file() and not (prompts_dir / f"{sid}_prompt.base.txt").is_file():
            shutil.copyfile(target, prompts_dir / f"{sid}_prompt.base.txt")
        target.write_text(prompt + "\n", encoding="utf-8")
        rows.append({"segment_id": sid, "scene_id": scene_key, "chars": len(anchors),
                     "length": len(prompt), "sections": prompt.count("[")})

    out = {
        "book_id": (book.get("book_id") or Path(a.book).name if a.book else None),
        "segments": rows,
        "segments_with_anchors": sum(1 for r in rows if r["chars"]),
        "prompt_dir": str(prompts_dir),
    }
    (a.out_dir / "prompt_enrich.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[enrich] {len(rows)} 段 -> {prompts_dir}")
    print(f"[enrich] 带角色锚点 {out['segments_with_anchors']}/{len(rows)} 段；"
          f"长度 {min(r['length'] for r in rows)}–{max(r['length'] for r in rows)} 字")
    for r in rows[:3]:
        print(f"  {r['segment_id']} {r['scene_id']} anchors={r['chars']} len={r['length']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
