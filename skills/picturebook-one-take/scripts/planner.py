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


def llm_plan(bible: dict, target_seconds: int = 150, retries: int = 2) -> dict:
    pages = "\n".join(f"{p['id']}：{p['text']}" for p in bible["pages"])
    chars = "\n".join(f"{c['id']}（{c['name']}，{c['role']}，首次出现 {c['first_page']}）：{c['look']}"
                      for c in bible["characters"])
    user = (f"书名：{bible['title']}\n目标时长：{target_seconds}s\n角色表：\n{chars}\n\n逐页原文：\n{pages}\n\n"
            f"请输出 JSON。")
    failures = []
    for attempt in range(retries + 1):
        msg = user
        if failures:
            msg += "\n\n上一版被门禁打回，必须修正：\n" + "\n".join("- " + f.get("message", "") for f in failures)
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
         use_llm: bool = False, script_in: str | None = None) -> dict:
    out = Path(out)
    if script_in:
        script = jread(script_in)
    elif use_llm:
        script = llm_plan(bible, target_seconds)
    elif book.endswith("cn01"):
        script = jread(Path(__file__).resolve().parents[1] / "examples" / "cn01_shot_script.json")
    else:
        raise RuntimeError("没有现成分镜；请加 --llm 或 --script")
    script["target_seconds"] = float(script.get("target_seconds") or target_seconds)
    result = validate_script(script, bible)
    jwrite(out / "shot_script.json", script)
    jwrite(out / "script_validation.json", result)
    if not result["passed"]:
        raise RuntimeError(f"shot_script 校验失败：{result['failures']}")
    return script
