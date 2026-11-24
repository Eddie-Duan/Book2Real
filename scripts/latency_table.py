#!/usr/bin/env python3
"""端到端首响打点表（Q9/Q34 口径）：话音结束 → 用户听到 TTS 首音。

口径说明：
* 外部 2s 时钟从"话音结束"起算；VAD 尾音 500ms 属内部指标，不计入（见 backend/docs/LATENCY_2S.md）。
* 本表离线跑：官方 wav → ASR（whisper）→ 检索/首句抽取 → TTS 首块。
* TTS 首块优先用 edge-tts（免费）实测；装不上就标 None，不拿别家数字冒充。

用法：
    python scripts/latency_table.py --book books/cn01 --audio "<官方音频目录>" --script "<脚本.txt>" --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from pathlib import Path

import book2real.evaluate as ev


def first_chunk_ms(text: str) -> float | None:
    """edge-tts 首个音频块到达时间（ms）；不可用返回 None。"""
    if not text.strip():
        return None
    try:
        import edge_tts
    except Exception:
        return None
    from book2real import tts as b2r_tts  # noqa: F401  仅确认模块在

    async def run() -> float | None:
        started = time.perf_counter()
        try:
            comm = edge_tts.Communicate(text[:60], "zh-CN-XiaoxiaoNeural")
            async for chunk in comm.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    return (time.perf_counter() - started) * 1000.0
        except Exception:
            return None
        return None

    try:
        return asyncio.run(run())
    except Exception:
        return None


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return round(ordered[k], 1)


def main() -> int:
    ap = argparse.ArgumentParser(description="2s 首响打点表")
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--budget-ms", type=float, default=2000.0)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    book = ev._book_dir(str(a.book))
    reader = ev._read_json(book / "reader" / "book.json", "reader/book.json")
    pages = [p for p in (reader.get("pages") or []) if isinstance(p, dict)]
    language = ev._language(ev._read_json(book / "book.json", "book.json"), reader)
    culture = ev.ASR_CULTURES.get(language, "zh-CN")
    rows = [r for r in ev.read_script(a.script) if r.get("kind") in ev.EVAL_KINDS][: a.limit]
    audio_index = ev._audio_index(a.audio)
    names = ev._character_names(book, pages)

    out_rows = []
    if not ev.recognizer_available(culture):
        print(f"[latency] ASR 不可用（{culture}），无法打点")
        return 2

    for row in rows:
        wav = audio_index.get(int(row["n"]))
        query = ev.script_query(row)
        if wav is None:
            continue
        t0 = time.perf_counter()
        text, reason = ev.recognize_wav(wav, culture, 30.0)
        asr_ms = (time.perf_counter() - t0) * 1000.0
        if reason:
            out_rows.append({"n": int(row["n"]), "query": query, "asr_ms": round(asr_ms, 1),
                             "reason": reason})
            continue
        t1 = time.perf_counter()
        hits = ev.retrieve(pages, text or query, char_names=names)
        retr_ms = (time.perf_counter() - t1) * 1000.0
        t2 = time.perf_counter()
        tts_ms = first_chunk_ms(text or query)
        if tts_ms is None:
            tts_ms = (time.perf_counter() - t2) * 1000.0
            tts_src = "n/a"
        else:
            tts_src = "edge-tts"
        total = asr_ms + retr_ms + (tts_ms or 0.0)
        out_rows.append({
            "n": int(row["n"]), "query": query, "asr_text": text,
            "top_page": hits[0]["page"] if hits else None,
            "asr_ms": round(asr_ms, 1), "retrieval_ms": round(retr_ms, 1),
            "tts_first_chunk_ms": round(tts_ms, 1) if tts_ms is not None else None,
            "tts_source": tts_src,
            "e2e_ms": round(total, 1),
            "within_budget": bool(total <= a.budget_ms),
        })

    ok = [r for r in out_rows if "e2e_ms" in r]
    asrs = [r["asr_ms"] for r in ok]
    rets = [r["retrieval_ms"] for r in ok]
    ttss = [r["tts_first_chunk_ms"] for r in ok if r.get("tts_first_chunk_ms") is not None]
    e2es = [r["e2e_ms"] for r in ok]
    report = {
        "book": str(a.book),
        "budget_ms": a.budget_ms,
        "samples": len(ok),
        "stages_p50_p95": {
            "asr": [pct(asrs, 50), pct(asrs, 95)],
            "retrieval": [pct(rets, 50), pct(rets, 95)],
            "tts_first_chunk": [pct(ttss, 50), pct(ttss, 95)] if ttss else None,
        },
        "e2e_p50_ms": pct(e2es, 50),
        "e2e_p95_ms": pct(e2es, 95),
        "within_budget": sum(1 for r in ok if r["within_budget"]),
        "within_budget_ratio": round(sum(1 for r in ok if r["within_budget"]) / len(ok), 4) if ok else None,
        "tts_source": sorted({r.get("tts_source") for r in ok}),
        "rows": out_rows,
    }
    out = a.out or (Path("runs") / book.name / "eval" / "latency_table.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(__import__("json").dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    st = report["stages_p50_p95"]
    print(f"[latency] {report['samples']} 条 | ASR P50/P95 {st['asr']} ms | 检索 {st['retrieval']} ms | "
          f"TTS 首块 {st['tts_first_chunk']} ({','.join(report['tts_source'])})")
    print(f"[latency] 端到端 P50 {report['e2e_p50_ms']} ms / P95 {report['e2e_p95_ms']} ms | "
          f"2s 内 {report['within_budget']}/{report['samples']} = {report['within_budget_ratio']}")
    print(f"[latency] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
