"""前后端契约兼容层。

backend 的 /api/ask 是唯一实现；既有阅读器（book2real/web/qa.js）还按老契约发
``{book, page:"p03", mode, query, entity, box, history}``，并按老字段渲染
``citations / source / fallback / socratic``。这里只做**请求归一并补响应字段**，
不复制任何问答逻辑。
"""
from __future__ import annotations

import re
from typing import Any, Optional

_PAGE_RE = re.compile(r"p?\s*0*(\d+)")


def page_to_int(value: Any) -> Optional[int]:
    if value is None or value == "" or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value or None
    match = _PAGE_RE.search(str(value))
    return int(match.group(1)) if match else None


def page_label(page: Any) -> str:
    number = page_to_int(page)
    return f"p{number:02d}" if number else ""


def normalize_ask_payload(payload: dict) -> dict:
    """把老/新两种请求都归一成 backend 的字段；未知字段原样保留。"""
    out = dict(payload or {})
    if not out.get("book_id"):
        out["book_id"] = out.get("book") or ""
    if not out.get("text"):
        out["text"] = out.get("query") or ""
    out["page"] = page_to_int(out.get("page"))
    if not out.get("session_id"):
        out["session_id"] = out.get("session") or "http"
    out.pop("book", None)
    out.pop("query", None)

    # 点画/点字：老前端用 entity+box（归一化 xywh），backend 用 element（xyxy）
    if not out.get("element") and isinstance(out.get("entity"), dict):
        entity = out["entity"]
        element: dict[str, Any] = {
            "name": str(entity.get("name") or ""),
            "info": str(entity.get("desc") or entity.get("info") or ""),
        }
        if out.get("page"):
            element["page"] = out["page"]
        box = out.get("box")
        if isinstance(box, list) and len(box) == 4:
            try:
                x, y, w, h = (float(v) for v in box)
                element["bbox"] = [x, y, x + w, y + h]
            except (TypeError, ValueError):
                pass
        if element["name"]:
            out["element"] = element
    return out


def decorate_answer(resp: dict, request_payload: dict, *, llm_name: str = "") -> dict:
    """在 backend 响应上补老前端仍会读的字段（citations/source/socratic/page）。"""
    out = dict(resp or {})
    citations = []
    for item in out.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        citations.append(
            {
                "page": page_label(item.get("page")),
                "quote": str(item.get("text") or ""),
                "kind": "text",
                "score": item.get("score"),
            }
        )
    out["citations"] = citations
    out["source"] = "rule" if "extractive" in (llm_name or "").lower() else "llm"
    out["fallback"] = None
    out["page"] = page_label(request_payload.get("page"))
    timings = out.get("timings") or {}
    latency = timings.get("first_audio") or timings.get("speech_to_first_audio_ms")
    if latency is None:
        latency = sum(v for v in timings.values() if isinstance(v, (int, float)))
    out["latency_ms"] = round(float(latency), 1) if latency else 0
    # backend 的回合管线没有苏格拉底状态机；保持旧字段形状，值为“不追问”
    out.setdefault("socratic", {"level": 0, "directive": "none", "line": ""})
    return out
