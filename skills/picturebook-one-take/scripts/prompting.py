# -*- coding: utf-8 -*-
"""Deterministic prompt assembly: eight blocks, iron rules twice, failure back-feed.

The prompt is deliberately compact. Full explanations live in SKILL.md; the model
only needs the enforceable clauses.
"""
from __future__ import annotations

from pathlib import Path

from contract import RULE_SIGNATURES

IRON_RULES = """[Iron Rules]
R1 禁止原书页当背景
R2 只允许角色表里的角色
R3 旁白与对白必须来自原文
R4 镜头必须有动机
R5 禁止 zoom 画面整体缩放
R6 场景之间必须自然衔接
R7 一次任务链、无人工后期
"""

CAMERA_LOCK = """[Camera Lock]
只做物理机位：横移/跟拍/摇摄/环绕/升降/真实推拉/切镜；禁止变焦、数字缩放、整幅等比放大缩小；近景用切镜。
"""

AUDIO_RULES = """[Audio]
旁白温柔年轻女声，慢速像妈妈讲故事；对白用角色自己的声音，嘴型同步；音效<>；不要生成任何背景音乐。
"""


def _cast_lines(chunk: dict, bible: dict, image_map: dict[str, int]) -> str:
    chars = {c["id"]: c for c in bible.get("characters", [])}
    lines = []
    for cid in chunk.get("cast") or []:
        c = chars.get(cid)
        if not c:
            continue
        idx = image_map.get(cid)
        tag = f"参考图片{idx}" if idx else "首帧场景板"
        lines.append(f"{c['name']}（{tag}；{c['look']}；{c['voice']}）")
    lines.append("只允许以上角色；禁止新增人物/动物，禁止双胞胎。")
    return "[Cast]\n" + "\n".join(lines)


def _shots_lines(chunk: dict) -> str:
    lines = ["[Shots]"]
    for i, s in enumerate(chunk.get("shots") or [], 1):
        bits = [s.get("camera") or "", s.get("action") or ""]
        if s.get("space"):
            bits.append(s["space"])
        if s.get("sound"):
            bits.append(s["sound"])
        lines.append(f"镜头{i}：" + "；".join(b for b in bits if b))
    return "\n".join(lines)


def _audio_lines(chunk: dict) -> str:
    narr = [n.get("text", "") for n in chunk.get("narration") or []]
    lines = []
    if narr:
        lines.append("旁白：" + " ".join("{" + t + "}" for t in narr))
    for d in chunk.get("dialogue") or []:
        name = (chunk.get("_names") or {}).get(d.get("speaker"), d.get("speaker"))
        voice = (chunk.get("_voices") or {}).get(d.get("speaker"), "")
        tone = d.get("tone") or ""
        lines.append(f"{name}（{voice}{('，' + tone) if tone else ''}）：{{{d.get('text')}}}")
    if chunk.get("sfx"):
        lines.append("音效：" + " ".join("<" + s + ">" for s in chunk["sfx"]))
    lines.append("（不要生成任何背景音乐；只保留旁白、对白、环境音和动作音效）")
    return "[Audio]\n" + "\n".join(lines)


def _self_check(chunk: dict, bible: dict) -> str:
    pages = "、".join(chunk.get("pages") or [])
    dlg = len(chunk.get("dialogue") or [])
    tr = (chunk.get("transition_out") or {}).get("type", "continuous_action")
    lines = ["[Self Check] 生成后机器逐条复查，任何一条不过就打回重生成："]
    lines.extend(RULE_SIGNATURES)
    lines.append(f"本段页 {pages}；对白 {dlg} 句；结尾 {tr}；画面无字/无水印/无原书页/无新增角色。")
    return "\n".join(lines)


def build_prompt(chunk: dict, bible: dict, image_map: dict[str, int] | None = None,
                 previous_failures: list[dict] | None = None,
                 location_ref: int | None = None) -> str:
    image_map = image_map or {}
    style = bible.get("style") or {}
    parts: list[str] = ["画面无任何字幕、文字、水印、logo。"]
    if previous_failures:
        fixes = [f.get("fix") or f.get("message") for f in previous_failures]
        parts.append("[PREVIOUS FAILURE — MUST FIX]\n" + "\n".join("- " + str(x) for x in fixes))
    parts.append(IRON_RULES)
    parts.append(f"[Style]\n{style.get('prompt', '')}\n负向：{style.get('negative', '')}")
    parts.append(_cast_lines(chunk, bible, image_map))
    scene = [f"地点：{chunk.get('location_name') or chunk.get('location') or ''}",
             f"页：{'、'.join(chunk.get('pages') or [])}"]
    if chunk.get("scene_note"):
        scene.append(chunk["scene_note"])
    if chunk.get("first_frame"):
        scene.append(f"首帧场景板：{chunk['first_frame']}；严格从它开始，人物/光线/构图/画风一致。")
    if location_ref:
        scene.append(f"场景参考图片{location_ref}：只参考地点/光线/画风，不照抄人物。")
    parts.append("[Scene]\n" + "\n".join(scene))
    parts.append(_shots_lines(chunk))
    parts.append(_audio_lines(chunk))
    parts.append(CAMERA_LOCK)
    parts.append(_self_check(chunk, bible))
    return "\n\n".join(parts)


def write_prompts(script: dict, bible: dict, out_dir: str | Path,
                  image_maps: dict[str, dict[str, int]] | None = None,
                  location_refs: dict[str, int] | None = None) -> dict:
    out = Path(out_dir) / "prompts"
    out.mkdir(parents=True, exist_ok=True)
    chars = {c["id"]: c for c in bible.get("characters", [])}
    written = {}
    for c in script.get("chunks", []):
        c = dict(c)
        c["_names"] = {cid: ch["name"] for cid, ch in chars.items()}
        c["_voices"] = {cid: ch.get("voice", "") for cid, ch in chars.items()}
        prompt = build_prompt(c, bible, image_map=(image_maps or {}).get(c["id"], {}),
                              location_ref=(location_refs or {}).get(c["id"]))
        p = out / f"{c['id']}.txt"
        p.write_text(prompt, encoding="utf-8")
        written[c["id"]] = str(p)
    return written
