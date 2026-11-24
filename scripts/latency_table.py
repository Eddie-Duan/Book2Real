#!/usr/bin/env python3
"""端到端首响打点表（Q9/Q34）：话音结束 → 用户听到 TTS 首音。

口径：
* 外部 2s 时钟从"话音结束"起算；VAD 尾音 500ms 属内部指标，不计入。
* 默认按**线上链路**打点：`.env` 里 `TTS_PROVIDER` / `ASR_PROVIDER` 配的什么就用什么
  （当前 = MiniMax 云端 ASR + MiniMax 云端 TTS）。`--provider local` 才退回 whisper + edge-tts。
* OCR 不在本链路（入库阶段，本地 RapidOCR）。

用法：
    python scripts/latency_table.py --book books/cn01 --audio "<官方音频目录>" \
        --script "<脚本.txt>" --limit 20            # 线上链路（MiniMax）
    python scripts/latency_table.py ... --provider local   # 纯本地对照
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import book2real.evaluate as ev  # noqa: E402


def _pcm_bytes(pcm) -> bytes:
    import numpy as np

    return np.clip(pcm, -1.0, 1.0).__mul__(32767.0).astype(np.int16).tobytes()


def online_providers():
    """按 .env 起后端那套 provider（MiniMax 云端 ASR + TTS）。"""
    sys.path.insert(0, str(REPO / "backend"))
    from app.config import Settings                     # noqa: PLC0415
    from app.providers.factory import build_asr, build_tts  # noqa: PLC0415

    settings = Settings()
    return settings, build_asr(settings), build_tts(settings)


def local_first_chunk_ms(text: str) -> float | None:
    """edge-tts 首个音频块（免费，仅作本地对照）。"""
    if not text.strip():
        return None
    try:
        import edge_tts  # noqa: PLC0415
    except Exception:
        return None

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


async def measure_online(rows, audio_index, pages, names, culture, asr, tts, budget_ms: float):
    """逐条：wav → MiniMax ASR → 检索 → MiniMax TTS 首块。"""
    out = []
    for row in rows:
        wav = audio_index.get(int(row["n"]))
        if wav is None:
            continue
        query = ev.script_query(row)
        try:
            pcm = ev._read_wav_16k(wav)
            rate = 16000                      # _read_wav_16k 已重采样到 16k
        except Exception as exc:  # noqa: BLE001
            out.append({"n": int(row["n"]), "query": query, "reason": f"wav: {exc}"})
            continue
        t0 = time.perf_counter()
        try:
            res = await asr.transcribe(_pcm_bytes(pcm), sample_rate=int(rate), language=None)
        except Exception as exc:  # noqa: BLE001
            out.append({"n": int(row["n"]), "query": query, "reason": f"asr: {exc}"})
            continue
        asr_ms = (time.perf_counter() - t0) * 1000.0
        text = getattr(res, "text", "") or ""
        t1 = time.perf_counter()
        hits = ev.retrieve(pages, text or query, char_names=names)
        retr_ms = (time.perf_counter() - t1) * 1000.0
        t2 = time.perf_counter()
        first_chunk = None
        try:
            async for chunk in tts.stream(text or query):
                if chunk:
                    first_chunk = (time.perf_counter() - t2) * 1000.0
                    break
        except Exception as exc:  # noqa: BLE001
            out.append({"n": int(row["n"]), "query": query, "reason": f"tts: {exc}"})
            continue
        total = asr_ms + retr_ms + (first_chunk or 0.0)
        out.append({
            "n": int(row["n"]), "query": query, "asr_text": text,
            "top_page": hits[0]["page"] if hits else None,
            "asr_ms": round(asr_ms, 1), "retrieval_ms": round(retr_ms, 1),
            "tts_first_chunk_ms": round(first_chunk, 1) if first_chunk is not None else None,
            "tts_source": "minimax", "e2e_ms": round(total, 1),
            "within_budget": bool(total <= budget_ms),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="2s 首响打点表")
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--budget-ms", type=float, default=2000.0)
    ap.add_argument("--provider", choices=["auto", "minimax", "local"], default="auto")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    book = ev._book_dir(str(a.book))
    reader = ev._read_json(book / "reader" / "book.json", "reader/book.json")
    pages = [p for p in (reader.get("pages") or []) if isinstance(p, dict)]
    book_json = ev._read_json(book / "book.json", "book.json")
    language = ev._language(book_json, reader)
    culture = ev.ASR_CULTURES.get(language, "zh-CN")
    rows = [r for r in ev.read_script(a.script) if r.get("kind") in ev.EVAL_KINDS][: a.limit]
    audio_index = ev._audio_index(a.audio)
    names = ev._character_names(book, pages)

    provider = a.provider
    online = None
    if provider in ("auto", "minimax"):
        # 注意：.env 是 app.config 导入时才灌进 os.environ 的，所以先起 Settings 再判 key
        try:
            online = online_providers()
        except Exception as exc:  # noqa: BLE001
            if provider == "minimax":
                print(f"[latency] 线上 provider 起不来：{exc}", file=sys.stderr)
                return 2
            online = None
    if online is not None:
        settings, asr, tts = online
        mode = f"online:{type(asr).__name__}+{type(tts).__name__}"
        out_rows = asyncio.run(measure_online(rows, audio_index, pages, names, culture,
                                              asr, tts, a.budget_ms))
    else:
        provider = "local"
        if not ev.recognizer_available(culture):
            print(f"[latency] 本地 ASR 不可用（{culture}）", file=sys.stderr)
            return 2
        mode = "local:whisper+edge-tts"
        out_rows = []
        for row in rows:
            wav = audio_index.get(int(row["n"]))
            query = ev.script_query(row)
            if wav is None:
                continue
            t0 = time.perf_counter()
            text, reason = ev.recognize_wav(wav, culture, 30.0)
            asr_ms = (time.perf_counter() - t0) * 1000.0
            if reason:
                out_rows.append({"n": int(row["n"]), "query": query, "reason": reason})
                continue
            t1 = time.perf_counter()
            hits = ev.retrieve(pages, text or query, char_names=names)
            retr_ms = (time.perf_counter() - t1) * 1000.0
            tts_ms = local_first_chunk_ms(text or query)
            total = asr_ms + retr_ms + (tts_ms or 0.0)
            out_rows.append({
                "n": int(row["n"]), "query": query, "asr_text": text,
                "top_page": hits[0]["page"] if hits else None,
                "asr_ms": round(asr_ms, 1), "retrieval_ms": round(retr_ms, 1),
                "tts_first_chunk_ms": round(tts_ms, 1) if tts_ms is not None else None,
                "tts_source": "edge-tts", "e2e_ms": round(total, 1),
                "within_budget": bool(total <= a.budget_ms),
            })

    ok = [r for r in out_rows if "e2e_ms" in r]
    asrs = [r["asr_ms"] for r in ok]
    rets = [r["retrieval_ms"] for r in ok]
    ttss = [r["tts_first_chunk_ms"] for r in ok if r.get("tts_first_chunk_ms") is not None]
    e2es = [r["e2e_ms"] for r in ok]
    report = {
        "book": str(a.book),
        "mode": mode,
        "budget_ms": a.budget_ms,
        "samples": len(ok),
        "failed": len(out_rows) - len(ok),
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
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    st = report["stages_p50_p95"]
    print(f"[latency] mode={mode} {report['samples']} 条（失败 {report['failed']}）")
    print(f"[latency] ASR {st['asr']} ms | 检索 {st['retrieval']} ms | TTS 首块 {st['tts_first_chunk']} ms")
    print(f"[latency] 端到端 P50 {report['e2e_p50_ms']} ms / P95 {report['e2e_p95_ms']} ms | "
          f"2s 内 {report['within_budget']}/{report['samples']} = {report['within_budget_ratio']}")
    print(f"[latency] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
