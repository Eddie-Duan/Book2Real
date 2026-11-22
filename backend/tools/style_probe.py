"""Skill 4 probe: inspect the style prompt, optionally call the configured LLM.

Dry-run by default. ``--go`` is the only way this script spends money.

Examples
--------
    python tools/style_probe.py --dry-run
    python tools/style_probe.py --go --question "为什么城堡倒了？" --question "Who is Xiaoman?"
    python tools/style_probe.py --go --question "这是什么？" --mode touch \
        --element-name "积木城堡" --element-info "用一块块彩色积木搭起来的高塔。"

The script never prints the API key.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.book.index import BookIndex, route_intent  # noqa: E402
from app.config import Settings  # noqa: E402
from app.pipeline.turn import build_messages  # noqa: E402
from app.skill4 import build_style_card, validate_answer  # noqa: E402

DEMO_BOOK = {
    "id": "demo",
    "title": "一起搭积木吧",
    "lang": "zh",
    "page_count": 3,
    "pages": [
        {"no": 1, "story_text": "下雨天，花花和小满在屋里搭积木。她们一块一块往上加。"},
        {"no": 2, "story_text": "小满打了个大喷嚏，积木城堡哗啦一下倒了。花花很生气。"},
        {"no": 3, "story_text": "后来，小满画了一张生日卡片，黄米团叼着卡片跑了出去。"},
    ],
    "elements": [],
    "script": {"items": []},
}


def load_book(path: str | None) -> dict:
    if not path:
        return DEMO_BOOK
    return json.loads(Path(path).read_text(encoding="utf-8"))


def call_llm(messages: list[dict], settings: Settings) -> tuple[str, float]:
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is empty; put it in Book2Real/.env (not in chat)")
    url = settings.llm_base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": False,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=settings.llm_timeout_s) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    elapsed = (time.perf_counter() - t0) * 1000
    text = data["choices"][0]["message"]["content"]
    return str(text).strip(), elapsed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="", help="backend 的 data/books/<id>/book.json")
    ap.add_argument("--question", action="append", default=[], help="可重复")
    ap.add_argument("--mode", choices=("qa", "touch"), default="qa")
    ap.add_argument("--element-name", default="")
    ap.add_argument("--element-info", default="")
    ap.add_argument("--dry-run", action="store_true", help="只打印 prompt（默认）")
    ap.add_argument("--go", action="store_true", help="真实调用 LLM，会产生费用")
    args = ap.parse_args()

    questions = args.question or ["为什么城堡倒了？"]
    book = load_book(args.book or None)
    index = BookIndex(book)
    card = build_style_card(book)
    print(f"[style] {json.dumps(card, ensure_ascii=False)}")

    for query in questions:
        element = None
        if args.mode == "touch":
            element = {"name": args.element_name or "积木城堡",
                       "info": args.element_info or "用一块块彩色积木搭起来的高塔。"}
        intent = route_intent(query, index, current_page=2, current_element=element)
        # The probe can also force a mode when route_intent does not classify it.
        if args.mode == "touch" and intent.get("intent") != "touch":
            intent["intent"] = "touch"
            intent["evidence"] = [{"element": element}]
        messages = build_messages(query, book, intent, [], 2, style_card=card)
        print("\n" + "=" * 72)
        print(f"[query] {query}  [mode] {intent.get('intent')}")
        print("[prompt]")
        print(messages[0]["content"])
        if not args.go:
            continue
        settings = Settings()
        try:
            answer, ms = call_llm(messages, settings)
        except Exception as exc:  # noqa: BLE001
            print(f"[llm] FAILED: {exc}")
            return 2
        answer_lang = (book.get("lang", "zh") if intent.get("intent") == "touch"
                       else intent.get("lang", "zh"))
        report = validate_answer(answer, answer_lang=answer_lang,
                                 mode=intent.get("intent", "qa"), card=card)
        print(f"[llm] {answer}  ({ms:.0f} ms)")
        print(f"[check] {json.dumps(report, ensure_ascii=False)}")
    if not args.go:
        print("\n[dry-run] no API call was made; pass --go to call the configured LLM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
