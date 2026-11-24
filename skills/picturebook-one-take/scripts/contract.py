# -*- coding: utf-8 -*-
"""Pre-generation contract: shot-script validation + prompt linter.

These are deterministic checks. They do not call any model and cost nothing.
Anything that fails here must stop before money is spent.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from common import contains_approx, normalize, overlap

REQUIRED_BLOCKS = ["[Iron Rules]", "[Style]", "[Cast]", "[Scene]", "[Shots]",
                   "[Audio]", "[Camera Lock]", "[Self Check]"]
RULE_SIGNATURES = [
    "R1 禁止原书页当背景",
    "R2 只允许角色表里的角色",
    "R3 旁白与对白必须来自原文",
    "R4 镜头必须有动机",
    "R5 禁止 zoom 画面整体缩放",
    "R6 场景之间必须自然衔接",
    "R7 一次任务链、无人工后期",
]
FORBIDDEN_SHOT_WORDS = ["变焦", "zoom", "数字缩放", "画面整体放大", "画面整体缩小",
                        "推近", "拉近", "拉远", "缩放画面", "放大画面"]
ALLOWED_TRANSITIONS = {"foreground_wipe", "object_into_lens", "whip_pan", "match_cut",
                       "through_door", "watercolor_bleed", "continuous_action"}
ALLOWED_CAMERA = ["横移", "跟拍", "摇摄", "环绕", "升降", "轨道", "切镜", "固定机位",
                  "向前移动", "向后移动", "dolly", "track", "pan", "orbit", "crane"]
ACTION_VERBS = ["放", "拿", "搭", "叠", "走", "跑", "蹲", "站", "坐", "转", "伸", "抱", "推", "拉",
                "捡", "递", "画", "看", "望", "笑", "哭", "喊", "跺", "扶", "拍", "摸", "爬", "跳",
                "挥", "打开", "关", "咬", "叼", "追", "飞", "滚", "倒", "塌", "点头", "摇头", "叹",
                "捡起", "放上", "放下", "抱起", "伸手", "转身", "低头", "抬头", "蹲下", "跑向"]


def _norm_keys(script: dict) -> list[str]:
    return sorted(script.keys())


def validate_script(script: dict, bible: dict) -> dict:
    """Return {"passed": bool, "checks": [...], "failures": [...]}."""
    failures: list[dict] = []
    checks: list[dict] = []

    def fail(code: str, msg: str, fix: str) -> None:
        failures.append({"code": code, "message": msg, "fix": fix})
        checks.append({"code": code, "passed": False, "message": msg})

    def ok(code: str, msg: str = "") -> None:
        checks.append({"code": code, "passed": True, "message": msg})

    # ---- schema
    for key in ("book_id", "target_seconds", "chunks"):
        if key not in script:
            fail("schema.missing", f"shot_script 缺少 {key}", f"补上 {key}")
    chunks = script.get("chunks") or []
    if not isinstance(chunks, list) or not chunks:
        fail("schema.chunks", "chunks 必须是非空数组", "至少给 3 个 chunk")
        return {"passed": False, "checks": checks, "failures": failures}
    ok("schema.missing")

    # ---- duration
    total = 0.0
    for c in chunks:
        d = float(c.get("duration") or 0)
        total += d
        if not (4 <= d <= 30):
            fail("duration.chunk", f"{c.get('id')} 时长 {d}s 不在 4–30s", "改到 4–30s")
        shots = c.get("shots") or []
        if not (2 <= len(shots) <= 5):
            fail("duration.shots", f"{c.get('id')} 镜头数 {len(shots)} 不在 2–5", "合并或拆到 2–5 镜")
        sd = sum(float(s.get("duration") or 0) for s in shots)
        if abs(sd - d) > 1.0:
            fail("duration.shot_sum", f"{c.get('id')} 镜头时长和 {sd} ≠ chunk {d}", "让镜头和等于 chunk 时长")
    if not (100 <= total <= 200):
        fail("duration.total", f"总时长 {total}s 不在 100–200s", "调整 chunk 数或时长")
    if total > 180:
        fail("duration.total_safe", f"总时长 {total}s 超过安全上限 180s（Q39 ≤3min）", "压到 ≤180s")
    ok("duration.total", f"{total}s")

    # ---- coverage
    pages = [p["id"] for p in bible.get("pages", [])]
    covered: dict[str, str] = {}
    for c in chunks:
        for p in c.get("pages") or []:
            if p not in pages:
                fail("coverage.unknown_page", f"{c.get('id')} 引用了不存在的页 {p}", "改成 bible 里的页 id")
            covered[p] = c.get("id")
    cover_pages = {p["id"] for p in bible.get("pages", []) if p.get("kind") == "cover"}
    missing = [p for p in pages if p not in covered and p not in cover_pages]
    if missing:
        fail("coverage.missing", f"未覆盖页：{missing}", "把缺的页并入相邻 chunk")
    else:
        ok("coverage.missing", f"{len(covered)}/{len(pages)} 页覆盖")

    # ---- dialogue fidelity + cast
    page_text = {p["id"]: p.get("text") or "" for p in bible.get("pages", [])}
    cast_ids = {c["id"] for c in bible.get("characters", [])}
    names = {c["name"]: c["id"] for c in bible.get("characters", [])}
    first_page = {c["id"]: c.get("first_page") for c in bible.get("characters", [])}
    for c in chunks:
        cid = c.get("id")
        pages_of = c.get("pages") or []
        source = "\n".join(page_text.get(p, "") for p in pages_of)
        for d in c.get("dialogue") or []:
            sp = d.get("speaker")
            if sp not in cast_ids:
                fail("cast.speaker", f"{cid} 对白说话人 {sp} 不在角色表", "改成角色表里的 id")
            if not contains_approx(source, d.get("text", "")):
                fail("dialogue.fidelity", f"{cid} 对白不在对应页原文：{d.get('text')}",
                     "改成原文原句，或把该页加进 chunk.pages")
        for n in c.get("narration") or []:
            t = n.get("text", "")
            if overlap(source, t) < 0.45 and not contains_approx(source, t, 0.8):
                fail("narration.fidelity", f"{cid} 旁白与原文重合度过低：{t}",
                     "用原文词句改写旁白，不要新增剧情")
        for cid2 in c.get("cast") or []:
            if cid2 not in cast_ids:
                fail("cast.unknown", f"{cid} 出现未知角色 {cid2}", "只保留角色表里的角色")
                continue
            fp = first_page.get(cid2)
            if fp and pages_of and not any(p >= fp for p in pages_of):
                fail("cast.too_early", f"{cid2} 最早出现页 {fp}，但 {cid} 的页都早于它",
                     "把该角色移到其首次出现页之后的 chunk")
        for sp in {d.get("speaker") for d in (c.get("dialogue") or [])}:
            if sp and sp not in (c.get("cast") or []):
                fail("cast.not_declared", f"{cid} 对白说话人 {sp} 未写进 cast", "补进 cast")

    # ---- camera / transitions
    for c in chunks:
        cid = c.get("id")
        shots = c.get("shots") or []
        text = " ".join(str(s.get("camera", "")) + " " + str(s.get("action", "")) for s in shots)
        for w in FORBIDDEN_SHOT_WORDS:
            if w in text:
                fail("camera.zoom_word", f"{cid} 镜头描述出现禁词「{w}」", "改成物理机位移动或切镜")
        if not any(k in text for k in ALLOWED_CAMERA):
            fail("camera.motivation", f"{cid} 没有任何有动机的机位/切镜", "至少写一个横移/跟拍/摇摄/环绕/切镜")
        for s in shots:
            act = normalize(s.get("action") or "")
            if len(act) < 8:
                fail("action.too_short", f"{cid}/{s.get('id')} 动作描述过短", "写清谁做什么、幅度、方向")
            elif not any(v in act for v in ACTION_VERBS):
                fail("action.generic", f"{cid}/{s.get('id')} 只有微动/情绪，没有看得见的动作动词",
                     "补一个具体动作：拿起/放下/搭/转身/走向/蹲下/抱起/跑/推/拉等")
        tr = (c.get("transition_out") or {}).get("type") or "continuous_action"
        if tr not in ALLOWED_TRANSITIONS:
            fail("transition.type", f"{cid} 转场 {tr} 不在白名单", "换成 foreground_wipe/object_into_lens/match_cut 等")
        if tr == "foreground_wipe" and not (c.get("transition_out") or {}).get("element"):
            fail("transition.element", f"{cid} 前景遮挡没写遮挡物", "写清树叶/气球/狗尾巴等具体元素")
    ok("camera.transition")

    # ---- audio
    for c in chunks:
        cid = c.get("id")
        if not (c.get("dialogue") or c.get("narration")):
            fail("audio.empty", f"{cid} 没有任何旁白/对白", "每 chunk 至少一句旁白或对白")
        if (c.get("music") or "none") != "none":
            fail("audio.bgm", f"{cid} 写了 BGM；绘本片默认不要 BGM", "music 设为 none")
    ok("audio.lines")

    return {"passed": not failures, "checks": checks, "failures": failures,
            "total_seconds": total, "pages_covered": covered}


def lint_prompt(prompt: str, chunk: dict, bible: dict) -> dict:
    failures: list[dict] = []
    checks: list[dict] = []

    def fail(code: str, msg: str, fix: str) -> None:
        failures.append({"code": code, "message": msg, "fix": fix})
        checks.append({"code": code, "passed": False, "message": msg})

    for b in REQUIRED_BLOCKS:
        if b not in prompt:
            fail("prompt.block", f"提示词缺少区块 {b}", f"补上 {b}")
    for sig in RULE_SIGNATURES:
        if prompt.count(sig) < 2:
            fail("prompt.repeat", f"铁律「{sig}」没有在开头和结尾各出现一次", "首尾各复制一遍铁律")
    # zoom words only inside [Shots]
    m = re.search(r"\[Shots\](.*?)\[Audio\]", prompt, re.S)
    shots = m.group(1) if m else ""
    for w in FORBIDDEN_SHOT_WORDS:
        if w in shots:
            fail("prompt.zoom_word", f"[Shots] 出现禁词「{w}」", "改成物理机位/切镜")
    # audio contract
    if not re.search(r"\{[^}]+\}", prompt):
        fail("prompt.audio", "提示词没有 {台词}", "旁白/对白用 {} 写进 [Audio]")
    if "不要生成任何背景音乐" not in prompt:
        fail("prompt.bgm", "提示词没有明确不要 BGM", "加「（不要生成任何背景音乐）」")
    # no raw-page background instruction
    for bad in ("以原书页作为背景", "用第", "把原图当", "分格小图当背景", "直接使用原书页"):
        if bad in prompt:
            fail("prompt.raw_page", f"提示词出现原书页用法「{bad}」", "只允许 assets/ 的干净场景板")
    if len(prompt) > 2000:
        fail("prompt.length", f"提示词过长 {len(prompt)} 字", "压到 2000 字以内，先删重复形容词")
    return {"passed": not failures, "checks": checks, "failures": failures, "length": len(prompt)}


def lint_all(prompt_dir: str | Path, script: dict, bible: dict) -> dict:
    out = {}
    for c in script.get("chunks", []):
        p = Path(prompt_dir) / f"{c['id']}.txt"
        if not p.exists():
            out[c["id"]] = {"passed": False,
                            "failures": [{"code": "prompt.missing", "message": str(p),
                                          "fix": "先跑 prompts 子命令"}]}
            continue
        out[c["id"]] = lint_prompt(p.read_text(encoding="utf-8"), c, bible)
    return {"passed": all(v.get("passed") for v in out.values()), "chunks": out}