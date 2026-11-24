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
只做物理机位：横移/跟拍/摇摄/环绕/升降/真实推拉/切镜；禁止变焦、数字缩放、整幅等比放大缩小；
不要用连续推拉从特写拉到全景；近景/特写一律用切镜。
"""

def _props_lines(chunk: dict, bible: dict, prop_map: dict[str, int] | None = None) -> str:
    ids = set(chunk.get("props") or [])
    pages = set(chunk.get("pages") or [])
    props = []
    for p in bible.get("props") or []:
        if p["id"] in ids or (not ids and pages.intersection(p.get("pages") or [])):
            props.append(p)
    lines = ["[Props]"]
    if not props:
        lines.append("本段没有关键道具；不要新增道具。")
    else:
        lines.append("道具锁定：以下道具的颜色、形状、数量、位置在整段中保持不变；"
                     "禁止把绿色长方体变成红色三角，禁止替换/新增道具。"
                     "所有屏幕、包装、招牌、海报上不得出现任何文字、字母、数字。")
        for p in props:
            idx = (prop_map or {}).get(p["id"])
            tag = f"（参考图片{idx}）" if idx else ""
            lines.append(f"- {p['name']}{tag}：{p['look']}")
    return "\n".join(lines)


def _voice_lock(chunk: dict, bible: dict) -> str:
    chars = {c["id"]: c for c in bible.get("characters", [])}
    narrator = (bible.get("narrator") or {}).get("voice") or "成年女性，幼儿园老师"
    lines = ["[Voice Lock]",
             f"旁白固定为：{narrator}。旁白与所有角色声音必须明显不同。",
             "角色声音固定，不许互换、不许张冠李戴："]
    for cid in chunk.get("cast") or []:
        c = chars.get(cid)
        if c:
            lines.append(f"- {c['name']}：{c['voice']}")
    lines.append("同一角色全片同一种声音；台词必须由对应角色说出；狗只发汪汪/呜呜声。")
    return "\n".join(lines)


AUDIO_CONTINUITY = """[Audio Continuity]
每个镜头的旁白/对白必须说完完整一句；段尾说完后留 0.4–0.6 秒静音或环境音，不要在半句时切断；下一段从完整句子开始。
最后一句在本段第 27 秒前说完，27–30 秒只留环境音/静音，不要再说话；段尾不要淡出/黑场，最后一帧保持正常亮度；下一段开头直接接这一帧。"""



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


SURFACE_TERMS = ["包装", "货架", "屏幕", "招牌", "海报", "标签", "收银台",
                 "packaging", "shelf", "screen", "sign", "poster", "label", "counter"]


def _shots_lines(chunk: dict) -> str:
    lines = ["[Shots]"]
    for i, s in enumerate(chunk.get("shots") or [], 1):
        bits = [s.get("camera") or "", s.get("action") or ""]
        if s.get("space"):
            bits.append(s["space"])
        if s.get("sound"):
            bits.append(s["sound"])
        text = " ".join(bits)
        # general fix: any text-prone surface must be blank / turned away
        if any(k in text.lower() for k in SURFACE_TERMS):
            bits.append("该表面必须保持空白：无文字、无字母、无数字、无logo；必要时背对镜头")
        lines.append(f"镜头{i}：" + "；".join(b for b in bits if b))
    return "\n".join(lines)


def _audio_lines(chunk: dict) -> str:
    narr = [n.get("text", "") for n in chunk.get("narration") or []]
    lines = []
    if narr:
        lines.append("旁白（幼儿园老师女声）：" + " ".join("{" + t + "}" for t in narr))
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
    lines.append(f"本段页 {pages}；对白 {dlg} 句；结尾 {tr}；道具颜色/形状不变；旁白与角色声音分开；段尾留静音；画面无字/无水印/无原书页/无新增角色。")
    return "\n".join(lines)


def build_prompt(chunk: dict, bible: dict, image_map: dict[str, int] | None = None,
                 previous_failures: list[dict] | None = None,
                 location_ref: int | None = None,
                 prop_map: dict[str, int] | None = None) -> str:
    image_map = image_map or {}
    style = bible.get("style") or {}
    parts: list[str] = ["画面无任何字幕、文字、水印、logo。"]
    if previous_failures:
        fixes = [f.get("fix") or f.get("message") for f in previous_failures]
        parts.append("[PREVIOUS FAILURE — MUST FIX]\n" + "\n".join("- " + str(x) for x in fixes))
    parts.append(IRON_RULES)
    parts.append(f"[Style]\n{style.get('prompt', '')}\n负向：{style.get('negative', '')}")
    parts.append(_cast_lines(chunk, bible, image_map))
    parts.append(_props_lines(chunk, bible, prop_map))
    scene = [f"地点：{chunk.get('location_name') or chunk.get('location') or ''}",
             f"页：{'、'.join(chunk.get('pages') or [])}"]
    if chunk.get("scene_note"):
        scene.append(chunk["scene_note"])
    scene.append("本段开头直接接上一段最后一帧，不要黑场/淡入；结尾说完最后一句再停。")
    if chunk.get("first_frame"):
        scene.append(f"首帧场景板：{chunk['first_frame']}；严格从它开始，人物/光线/构图/画风一致。")
    if location_ref:
        scene.append(f"场景参考图片{location_ref}：只参考地点/光线/画风，不照抄人物。")
    parts.append("[Scene]\n" + "\n".join(scene))
    parts.append(_shots_lines(chunk))
    parts.append(_audio_lines(chunk))
    parts.append(_voice_lock(chunk, bible))
    parts.append(AUDIO_CONTINUITY)
    parts.append(CAMERA_LOCK)
    parts.append(_self_check(chunk, bible))
    return "\n\n".join(parts)


def write_prompts(script: dict, bible: dict, out_dir: str | Path,
                  image_maps: dict[str, dict[str, int]] | None = None,
                  location_refs: dict[str, int] | None = None,
                  prop_maps: dict[str, dict[str, int]] | None = None) -> dict:
    out = Path(out_dir) / "prompts"
    out.mkdir(parents=True, exist_ok=True)
    chars = {c["id"]: c for c in bible.get("characters", [])}
    written = {}
    for c in script.get("chunks", []):
        c = dict(c)
        c["_names"] = {cid: ch["name"] for cid, ch in chars.items()}
        c["_voices"] = {cid: ch.get("voice", "") for cid, ch in chars.items()}
        prompt = build_prompt(c, bible, image_map=(image_maps or {}).get(c["id"], {}),
                              location_ref=(location_refs or {}).get(c["id"]),
                              prop_map=(prop_maps or {}).get(c["id"], {}))
        p = out / f"{c['id']}.txt"
        p.write_text(prompt, encoding="utf-8")
        written[c["id"]] = str(p)
    return written