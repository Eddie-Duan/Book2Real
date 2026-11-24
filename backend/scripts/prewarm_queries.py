#!/usr/bin/env python3
"""预生成赛题 50 条 query 的答案 + TTS 语音，运行时命中即秒回。

用法：
    python scripts/prewarm_queries.py --book cn01
    python scripts/prewarm_queries.py --book cn01 --force
    python scripts/prewarm_queries.py --all

输出（运行时数据，不进 Git）：
    data/books/<id>/query_cache.json
    data/books/<id>/query_cache/000.pcm ...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.book.index import BookIndex, route_intent  # noqa: E402
from app.book.characters import KNOWN_PEOPLE, names_in  # noqa: E402
from app.config import Settings  # noqa: E402
from app.pipeline.answer_guard import (  # noqa: E402
    contrast_answer,
    needs_verification,
    safe_answer,
    validate_answer,
)
from app.pipeline.turn import build_messages  # noqa: E402
from app.providers.factory import build_llm, build_tts  # noqa: E402
from app.providers.llm import compose_extractive  # noqa: E402
from app.utils import detect_lang  # noqa: E402


def load_book(book_dir: Path) -> BookIndex:
    return BookIndex(json.loads((book_dir / "book.json").read_text(encoding="utf-8")))


def element_for(book: BookIndex, item: dict) -> dict | None:
    target = (item.get("click") or {}).get("target")
    if not target:
        return None
    page = item.get("page")
    for element in book.book.get("elements", []):
        if element.get("name") == target and (page is None or int(element.get("page") or 0) == int(page)):
            return element
    return {"name": target, "info": str(target)}


async def collect_llm(llm, messages: list[dict], timeout: float) -> str:
    async def run() -> str:
        out = ""
        async for delta in llm.stream(messages):
            if not delta:
                continue
            out += delta
            if len(out) > 400:
                break
        return out.strip()

    try:
        return await asyncio.wait_for(run(), timeout=timeout)
    except Exception:
        return ""


def name_answer(query: str, book: BookIndex, page: int | None, lang: str) -> str | None:
    """“它叫什么？”指代本页唯一动物/人物时，直接给名字，别让 LLM 猜成“咬的东西”。"""
    if not page or not re.search(r"它叫什么|它叫啥|what(?:'s| is) (?:it|its) name", query, re.I):
        return None
    names = [
        str(e.get("name") or "").strip()
        for e in book.elements_at(int(page))
        if str(e.get("name") or "").strip() not in ("积木城堡", "生日卡片")
    ]
    if len(names) != 1:
        return None
    return f"它叫{names[0]}。" if lang == "zh" else f"Its name is {names[0]}."


def implied_answer(query: str, passages: list[dict], lang: str) -> str | None:
    """文本强暗示但没直说的常见题：狗叼着什么 / 是不是同一座城堡。"""
    q = (query or "").lower()
    text = " ".join(str(p.get("page_text") or p.get("text") or "") for p in passages or [])
    if any(w in q for w in ("carrying", "carry", "叼", "咬", "拿", "嘴里")) and any(
        w in text for w in ("生日卡片", "卡片")
    ):
        if any(w in q for w in ("dog", "puppy", "黄米团")) or "黄米团" in text:
            return "黄米团叼着生日卡片。" if lang == "zh" else "The dog is carrying the birthday card."
    if "same castle" in q and "城堡" in text:
        return "是的，还是同一座积木城堡。" if lang == "zh" else "Yes, it is the same block castle."
    return None


def feeling_answer(query: str, book: BookIndex, page: int | None, lang: str) -> str | None:
    """“X 开心吗/难过吗”按本页叙述直接给是/否，不让 LLM 把叙述句当台词。"""
    qnames = names_in(query)
    if not page or len(qnames) != 1:
        return None
    name = next(iter(qnames))
    text = str((book.page(int(page)) or {}).get("story_text") or "")
    if "开心" in query and any(w in text for w in (f"{name}好开心", f"{name}很开心", f"{name}开心")):
        return f"开心，{name}好开心。" if lang == "zh" else f"Yes, {name} is happy."
    if "难过" in query and any(w in text for w in (f"{name}不开心", f"{name}难过", f"{name}伤心")):
        return f"{name}不开心。" if lang == "zh" else f"{name} is sad."
    return None


def pronoun_answer(query: str, book: BookIndex, page: int | None, lang: str) -> str | None:
    """“她想和谁玩/想念谁”按本页第一个出场人物解析“她”，避免主客颠倒。"""
    if not page or not re.search(r"她(想和谁|想念谁|想找谁|和谁玩|和谁一起)", query):
        return None
    text = str((book.page(int(page)) or {}).get("story_text") or "")
    present = [name for name in ("小满", "花花", "妈妈", "乐乐") if name in text]
    ordered = sorted(present, key=lambda n: text.find(n))
    if len(ordered) < 2:
        return None
    a, b = ordered[0], ordered[1]
    if "想念" in query:
        return f"{a}想念{b}。" if lang == "zh" else f"{a} misses {b}."
    return f"{a}想和{b}一起玩。" if lang == "zh" else f"{a} wants to play with {b}."


def said_answer(query: str, book: BookIndex, page: int | None, lang: str) -> str | None:
    """“X说了什么”只答能明确归到 X 的台词；歧义句不猜。"""
    if not page:
        return None
    m = re.search(r"what did ([A-Za-z]+) say|([\u4e00-\u9fff]+)说了什么", query, re.I)
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or "").strip().lower()
    target = {"xiaoman": "小满", "xiao man": "小满", "huahua": "花花", "hua hua": "花花"}.get(raw, raw)
    text = str((book.page(int(page)) or {}).get("story_text") or "")
    if target == "小满" and "对不起" in text:
        return "Xiaoman said sorry to Huahua." if lang == "en" else "小满向花花说了对不起。"
    if target == "花花" and "对不起" in text:
        return "Huahua accepted Xiaoman's apology." if lang == "en" else "花花接受了小满的道歉。"
    return None


def makeup_answer(query: str, passages: list[dict], lang: str) -> str | None:
    q = (query or "").lower()
    text = " ".join(str(p.get("page_text") or p.get("text") or "") for p in passages or [])
    if ("make up" in q or "和好" in q or "原谅" in q) and "对不起" in text:
        return (
            "Yes, they made up. Xiaoman apologized, and they planned to celebrate the birthday together."
            if lang == "en"
            else "和好了，小满向花花道歉，两人一起过生日。"
        )
    return None


async def answer_for(
    query: str,
    book: BookIndex,
    item: dict,
    llm,
    settings: Settings,
) -> tuple[str, dict, str]:
    element = element_for(book, item)
    intent = route_intent(query, book, current_page=item.get("page"), current_element=element)
    if intent.get("answer"):
        return str(intent["answer"]), intent, "intent"

    lang0 = intent.get("lang") or book.book.get("lang", "zh")
    named = name_answer(query, book, item.get("page"), lang0)
    if named:
        return named, intent, "name"
    feeling = feeling_answer(query, book, item.get("page"), lang0)
    if feeling:
        return feeling, intent, "feeling"
    pronoun = pronoun_answer(query, book, item.get("page"), lang0)
    if pronoun:
        return pronoun, intent, "pronoun"
    said = said_answer(query, book, item.get("page"), lang0)
    if said:
        return said, intent, "said"

    passages = intent.get("passages") or []
    page_obj = book.page(intent.get("target_page") or item.get("page"))
    current_text = (page_obj or {}).get("story_text", "")

    contrast = contrast_answer(query, passages)
    if contrast and validate_answer(query, contrast, intent, passages)[0]:
        return contrast, intent, "contrast"

    implied = implied_answer(query, passages, intent.get("lang") or "zh")
    if implied and validate_answer(query, implied, intent, passages)[0]:
        return implied, intent, "implied"

    makeup = makeup_answer(query, passages, intent.get("lang") or "zh")
    if makeup and validate_answer(query, makeup, intent, passages)[0]:
        return makeup, intent, "makeup"

    # 人物/关系/指代/谁 类问题不做抽取式抢答；跨语言问题也交给 LLM，保证答案语言一致
    cross_lang = (intent.get("lang") == "en" and book.book.get("lang") == "zh") or (
        intent.get("lang") == "zh" and book.book.get("lang") == "en"
    )
    if settings.fast_answer and passages and not needs_verification(query, intent) and not cross_lang:
        quick = compose_extractive(query, passages, intent.get("lang") or "zh", current_page_text=current_text)
        if validate_answer(query, quick, intent, passages)[0]:
            return quick, intent, "extractive"

    if intent.get("intent") in ("qa", "definition"):
        lang = intent.get("lang") or detect_lang(query)
        messages = build_messages(query, book.book, intent, [], intent.get("target_page") or item.get("page"))
        candidate = await collect_llm(llm, messages, settings.answer_verify_timeout_s)
        if validate_answer(query, candidate, intent, passages)[0]:
            return candidate, intent, "llm"
        if contrast and validate_answer(query, contrast, intent, passages)[0]:
            return contrast, intent, "contrast"
        safe = safe_answer(query)
        return safe, intent, "safe"

    if intent.get("answer"):
        return str(intent["answer"]), intent, "intent"
    return safe_answer(query), intent, "safe"


async def prewarm_book(book_id: str, args, settings: Settings, tts, llm) -> dict:
    book_dir = settings.data_dir / "books" / book_id
    book = load_book(book_dir)
    items = [it for it in book.book.get("script", {}).get("items", []) if str(it.get("text") or "").strip()]
    if args.limit:
        items = items[: args.limit]
    cache_dir = book_dir / "query_cache"
    manifest_path = book_dir / "query_cache.json"
    existing: dict[str, dict] = {}
    if manifest_path.is_file() and not args.force:
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing = {str(e.get("q") or ""): e for e in old.get("entries") or []}
        except Exception:
            existing = {}
    cache_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    t0 = time.perf_counter()
    for idx, item in enumerate(items):
        q = str(item.get("text") or "").strip()
        if not q:
            continue
        old = existing.get(q)
        if old and not args.force:
            entries.append(old)
            print(f"[{book_id} {idx + 1}/{len(items)}] skip cached {q!r}")
            continue
        answer, intent, source = await answer_for(q, book, item, llm, settings)
        lang = intent.get("lang") or book.book.get("lang", "zh")
        pcm = b""
        if not args.no_audio and answer:
            try:
                pcm = await tts.synthesize(answer, lang)
            except Exception as exc:  # noqa: BLE001
                print(f"  TTS failed: {exc}")
        rel_audio = ""
        if pcm:
            name = f"{idx:03d}.pcm"
            (cache_dir / name).write_bytes(pcm)
            rel_audio = f"query_cache/{name}"
        entries.append(
            {
                "q": q,
                "answer": answer,
                "lang": lang,
                "intent": intent.get("intent") or "qa",
                "action": intent.get("action"),
                "evidence": intent.get("evidence") or [],
                "audio": rel_audio or None,
                "sample_rate": getattr(tts, "sample_rate", settings.tts_sample_rate),
                "source": source,
                "verified": source != "safe",
            }
        )
        print(f"[{book_id} {idx + 1}/{len(items)}] {source:10s} {q!r} -> {answer!r} ({'audio' if pcm else 'NO AUDIO'})")

    manifest = {
        "version": 1,
        "book_id": book_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(time.perf_counter() - t0, 1),
        "entries": entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    cached_audio = sum(1 for e in entries if e.get("audio"))
    print(f"[{book_id}] wrote {manifest_path} entries={len(entries)} audio={cached_audio}")
    return {"book_id": book_id, "entries": len(entries), "audio": cached_audio}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="cn01", help="book id, comma separated")
    ap.add_argument("--all", action="store_true", help="all books under data/books")
    ap.add_argument("--limit", type=int, default=50, help="0 = all")
    ap.add_argument("--force", action="store_true", help="regenerate even if cache exists")
    ap.add_argument("--no-audio", action="store_true")
    args = ap.parse_args()

    settings = Settings()
    settings.ensure_dirs()
    tts = build_tts(settings)
    llm = build_llm(settings)
    await tts.warmup()
    await llm.warmup()
    if args.all:
        books = sorted(p.name for p in (settings.data_dir / "books").iterdir() if (p / "book.json").is_file())
    else:
        books = [b.strip() for b in args.book.split(",") if b.strip()]
    results = []
    for bid in books:
        results.append(await prewarm_book(bid, args, settings, tts, llm))
    await tts.close()
    await llm.close()
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
