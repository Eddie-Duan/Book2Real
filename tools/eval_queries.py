# -*- coding: utf-8 -*-
"""批量跑赛题 Query 音频：ASR → 意图/问答 → 首音耗时，输出 JSON/CSV 供人工核对。

先启动后端并入库：
  python tools/eval_queries.py --book cn01 --base http://127.0.0.1:8790 --out data/eval_cn01.json
  python tools/eval_queries.py --book en01 --limit 10
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8790")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--out", default="")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    book_dir = ROOT / "data" / "books" / args.book
    book = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
    items = {int(i["no"]): i for i in book.get("script", {}).get("items", [])}
    wavs = sorted((book_dir / "queries").glob("*.wav"))
    if args.start:
        wavs = wavs[args.start :]
    if args.limit:
        wavs = wavs[: args.limit]

    rows = []
    started = time.time()
    with httpx.Client(timeout=args.timeout) as client:
        for idx, wav in enumerate(wavs, 1):
            no = int(wav.stem)
            item = items.get(no, {})
            try:
                with wav.open("rb") as fh:
                    resp = client.post(
                        args.base.rstrip("/") + "/api/ask",
                        data={"book_id": args.book, "session_id": f"eval-{args.book}", "page": str(item.get("page") or "")},
                        files={"audio": (wav.name, fh, "audio/wav")},
                    )
                resp.raise_for_status()
                data = resp.json()
                row = {
                    "no": no,
                    "script": item.get("text", ""),
                    "type": item.get("type", ""),
                    "page": item.get("page"),
                    "asr": data.get("asr_text", ""),
                    "asr_lang": data.get("asr_lang", ""),
                    "answer": data.get("answer", ""),
                    "intent": (data.get("action") or {}).get("type") if isinstance(data.get("action"), dict) else "",
                    "action_page": (data.get("action") or {}).get("page") if isinstance(data.get("action"), dict) else None,
                    "first_audio_ms": (data.get("metrics") or {}).get("first_audio_after_speech_ms"),
                    "hit_2s": (data.get("metrics") or {}).get("hit"),
                    "used_filler": (data.get("metrics") or {}).get("used_filler"),
                }
            except Exception as exc:  # noqa: BLE001
                row = {"no": no, "script": item.get("text", ""), "error": f"{type(exc).__name__}: {exc}"}
            rows.append(row)
            print(f"[{idx}/{len(wavs)}] {no:02d} asr={row.get('asr','')!r} first={row.get('first_audio_ms')}ms answer={row.get('answer','')[:40]!r}")

    latencies = [float(r["first_audio_ms"]) for r in rows if r.get("first_audio_ms") is not None]
    summary = {
        "book": args.book,
        "count": len(rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "asr_empty": sum(1 for r in rows if not r.get("asr")),
        "hit_2s": sum(1 for r in rows if r.get("hit_2s")),
        "hit_ratio": round(sum(1 for r in rows if r.get("hit_2s")) / max(1, len(rows)), 4),
        "filler": sum(1 for r in rows if r.get("used_filler")),
        "latency_p50": round(statistics.median(latencies), 1) if latencies else None,
        "latency_p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 1) if latencies else None,
        "elapsed_s": round(time.time() - started, 1),
    }
    out = Path(args.out) if args.out else ROOT / "data" / f"eval_{args.book}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    csv_path = out.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["no"])
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("saved:", out, "and", csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
