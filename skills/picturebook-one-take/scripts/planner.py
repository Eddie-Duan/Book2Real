# -*- coding: utf-8 -*-
"""LLM story edit (DeepSeek flash) with contract retry; hand-authored example fallback."""
from __future__ import annotations

import json
from pathlib import Path

from common import DEEPSEEK, DEEPSEEK_KEY, DEEPSEEK_MODEL, http_json, jread, jwrite
from contract import validate_script

SYSTEM = """你是儿童绘本动画导演。把一本绘本改编成 100–180 秒、5–6 个 chunk 的连续长镜头脚本。
只输出 JSON，不要解释。硬规则：
1) 每个 chunk 4–30 秒，2–4 个镜头；总时长 100–180。
2) 每页正文都要被某个 chunk 的 pages 覆盖（封面可不算）。
3) 对白必须逐字来自对应页原文（去掉引号后是原文子串）；旁白必须大量使用原文词句，不许新增剧情/人物。
4) 只允许角色表里的角色；角色不能早于其首次出现页。
5) 镜头运动只写物理机位：横移/跟拍/摇摄/环绕/升降/切镜；禁止 zoom、变焦、推近、拉远、数字缩放。
6) 每个 chunk 结尾写 transition_out：foreground_wipe/object_into_lens/whip_pan/match_cut/through_door/watercolor_bleed/continuous_action；换场景必须用前五种之一，并写具体遮挡物。
7) 每个 chunk 至少一句旁白或对白；music 固定 "none"；不要 BGM。
8) 旁白温柔、短句、儿童能听懂；对白保留原文语气。
JSON 结构：
{"book_id":"...","target_seconds":150,"language":"zh","chunks":[
 {"id":"chunk_01","duration":30,"pages":["p02","p03"],"cast":["huahua","xiaoman"],
  "location":"room_rain","location_name":"下雨天的儿童房","scene_note":"...",
  "shots":[{"id":"s1","duration":10,"camera":"...","action":"...","space":"...","sound":"..."}],
  "narration":[{"text":"..."}],
  "dialogue":[{"speaker":"huahua","text":"...","tone":"..."}],
  "sfx":["..."],"music":"none",
  "transition_out":{"type":"continuous_action","element":"...","note":"..."}}
]}"""


def _style_block(card: dict) -> str:
    """把风格卡渲染成"旁白/对白的腔"，只读数据、不依赖 backend。

    注意：skill4 的 ``style_prompt`` 是给**问答**用的（"先回答问题…"），
    直接拿来指导分镜会跑偏；所以这里只取卡的字段，按叙述口径重写。
    """
    samples = card.get("samples") or []
    unit = card.get("unit") or "字"
    lines = ["【这本书的叙述声音】（旁白与对白都要贴这个腔）",
             f"- 叙述人称：{card.get('pov') or '第三人称'}",
             f"- 平均句长：约 {card.get('avg_sentence_len') or '?'} {unit}；短句、具体、有画面。"]
    if card.get("connectors"):
        lines.append(f"- 可用的连接词：{'、'.join(card['connectors'])}")
    if card.get("onomatopoeia"):
        lines.append(f"- 可用的拟声词：{'、'.join(card['onomatopoeia'])}")
    if samples:
        lines.append("- 照这些原文句子的长度和语气写（只学腔，不要整句照抄）：")
        lines += [f"  {i + 1}. {s}" for i, s in enumerate(samples)]
    lines.append("- 禁止：百科式解释、总结腔、成语堆砌；不要新增原文没有的人物或情节。")
    return "\n".join(lines)


def _user_prompt(bible: dict, target_seconds: int, style_text: str = "") -> str:
    """分镜请求的 user prompt；`style_text` 是 skill4 导出的"这本书的叙述声音"。"""
    pages = "\n".join(f"{p['id']}：{p['text']}" for p in bible["pages"])
    chars = "\n".join(f"{c['id']}（{c['name']}，{c['role']}，首次出现 {c['first_page']}）：{c['look']}"
                      for c in bible["characters"])
    prompt = (f"书名：{bible['title']}\n目标时长：{target_seconds}s\n角色表：\n{chars}\n\n逐页原文：\n{pages}\n\n"
              f"请输出 JSON。")
    if style_text.strip():
        prompt += ("\n\n" + style_text.strip()
                   + "\n（旁白与对白必须贴着这本书的句式、语气和具体程度；不要写成百科或说明书。）")
    return prompt


def llm_plan(bible: dict, target_seconds: int = 150, retries: int = 2,
             style_text: str = "") -> dict:
    failures = []
    for attempt in range(retries + 1):
        msg = _user_prompt(bible, target_seconds, style_text)
        if failures:
            msg += "\n\n上一版被门禁打回，必须修正：\n" + "\n".join("- " + f.get("message", "") for f in failures)
        if not DEEPSEEK_KEY:
            raise RuntimeError("没有 DEEPSEEK_API_KEY：要么配 key 走 LLM 分镜，要么用 --script/--from-example")
        status, data = http_json("POST", DEEPSEEK + "/v1/chat/completions", {
            "model": DEEPSEEK_MODEL, "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": msg}],
            "temperature": 0.4, "max_tokens": 5000,
            "response_format": {"type": "json_object"}}, DEEPSEEK_KEY, timeout=180)
        if status != 200:
            raise RuntimeError(f"DeepSeek plan HTTP {status}: {json.dumps(data, ensure_ascii=False)[:500]}")
        text = data["choices"][0]["message"]["content"]
        try:
            script = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            failures = [{"message": f"JSON 解析失败：{exc}"}]
            continue
        result = validate_script(script, bible)
        if result["passed"]:
            return script
        failures = result["failures"]
    raise RuntimeError(f"LLM plan failed contract: {failures}")


def plan(book: str, bible: dict, out: str | Path, target_seconds: int = 150,
         use_llm: bool = True, script_in: str | None = None,
         style: str | None = None, from_example: bool = False,
         max_attempts: int = 2) -> dict:
    """分镜：默认**现场调 LLM**；预写脚本必须显式 --script 或 --from-example。

    以前对 cn01 有一条默认捷径（直接读 examples/cn01_shot_script.json），
    导致"看代码像通用 skill、跑起来只有那一本书能用"。现在默认走 LLM，
    示例只作回归基线，且必须显式点名。
    """
    out = Path(out)
    style_text = ""
    if style:
        card = jread(style)
        style_text = _style_block(card)
    if script_in:
        script = jread(script_in)
    elif from_example:
        ex = Path(__file__).resolve().parents[1] / "examples" / f"{Path(book).name}_shot_script.json"
        if not ex.is_file():
            raise RuntimeError(f"没有 {ex.name} 这份示例；请改用 --script 或默认的 LLM 分镜")
        script = jread(ex)
    else:
        script = llm_plan(bible, target_seconds,
                          retries=max(0, int(max_attempts) - 1), style_text=style_text)
    script["target_seconds"] = float(script.get("target_seconds") or target_seconds)
    result = validate_script(script, bible)
    jwrite(out / "shot_script.json", script)
    jwrite(out / "script_validation.json", result)
    if not result["passed"]:
        raise RuntimeError(f"shot_script 校验失败：{result['failures']}")
    return script
