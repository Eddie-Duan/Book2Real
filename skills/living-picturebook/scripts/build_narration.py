#!/usr/bin/env python3
"""按 audio_plan.json 合成旁白/对白，并铺到时间轴上，输出 narration.mp3。

- 合成：MiniMax T2A v2（.env 里的 MINIMAX_*）或 edge-tts（离线兜底）
- 缓存：按 (voice, rate, pitch, text) 的哈希存 .tmp/tts_cache/*.mp3，重跑不重复花钱
- 对齐：每条按 start_sec 用 ffmpeg adelay 铺到静音底轨上，最后 amix
- 报告：计划时长 vs 实际时长、溢出多少、总漂移

用法：
    python build_narration.py --audio-plan .tmp/audio_plan_cn01.json \
        --out .tmp/narration_cn01.mp3 [--provider minimax|edge] [--limit 5]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

CACHE = Path(".tmp/tts_cache")
MINIMAX_OK = {"code": 0}


def load_env(path: Path = Path(".env")) -> dict:
    env = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def cache_key(line: dict) -> str:
    raw = f"{line['voice']}|{line['rate']}|{line['pitch']}|{line['text']}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def tts_minimax(text: str, voice: str, rate: str, pitch: str, env: dict, out: Path) -> float:
    host = (env.get("MINIMAX_HOST") or "https://api.minimaxi.com").rstrip("/")
    key = env.get("MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("MINIMAX_API_KEY 为空")
    speed = 1.0
    if rate.endswith("%"):
        try:
            speed = 1.0 + float(rate[:-1]) / 100.0
        except ValueError:
            speed = 1.0
    try:
        pitch_i = int(str(pitch).replace("Hz", "").replace("+", ""))
    except ValueError:
        pitch_i = 0
    payload = {
        "model": env.get("MINIMAX_TTS_MODEL") or "speech-2.8-turbo",
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": voice, "speed": speed, "vol": 1.0, "pitch": pitch_i},
        "audio_setting": {"sample_rate": int(env.get("MINIMAX_SAMPLE_RATE") or 24000), "format": "mp3"},
    }
    group = env.get("MINIMAX_GROUP_ID")
    url = f"{host}/v1/t2a_v2" + (f"?GroupId={group}" if group else "")
    r = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = (r.json().get("data") or {}).get("audio") or ""
    if not data:
        raise RuntimeError("响应里没有 audio")
    out.write_bytes(bytes.fromhex(data))
    return ffprobe_duration(out)


def tts_edge(text: str, voice: str, out: Path) -> float:
    import asyncio

    import edge_tts

    lang_voice = voice if voice.startswith(("zh-", "en-")) else "zh-CN-XiaoxiaoNeural"

    async def run():
        await edge_tts.Communicate(text, lang_voice).save(str(out))

    asyncio.run(run())
    return ffprobe_duration(out)


def mix(lines: list[dict], total_sec: float, out: Path) -> None:
    inputs = []
    filters = []
    for i, line in enumerate(lines):
        inputs += ["-i", line["file"]]
        ms = int(round(line["start_sec"] * 1000))
        filters.append(f"[{i}:a]adelay={ms}|{ms},volume=1.0[a{i}]")
    mix_inputs = "".join(f"[a{i}]" for i in range(len(lines)))
    filter_complex = ";".join(filters) + f";{mix_inputs}amix=inputs={len(lines)}:normalize=0[aout]"
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", filter_complex,
           "-map", "[aout]", "-t", str(total_sec), "-ar", "24000", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])


def main() -> int:
    ap = argparse.ArgumentParser(description="audio_plan -> narration.mp3")
    ap.add_argument("--audio-plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--provider", choices=["minimax", "edge"], default="minimax")
    ap.add_argument("--limit", type=int, default=0, help="只做前 N 条（调试用）")
    a = ap.parse_args()

    plan = json.loads(Path(a.audio_plan).read_text(encoding="utf-8"))
    env = load_env()
    CACHE.mkdir(parents=True, exist_ok=True)
    lines = plan.get("lines") or []
    if a.limit:
        lines = lines[: a.limit]

    t0 = time.time()
    rows = []
    for i, line in enumerate(lines, 1):
        key = cache_key(line)
        f = CACHE / f"{key}.mp3"
        hit = f.is_file() and f.stat().st_size > 0
        if not hit:
            try:
                if a.provider == "minimax":
                    dur = tts_minimax(line["text"], line["voice"], line["rate"], line["pitch"], env, f)
                else:
                    dur = tts_edge(line["text"], line["voice"], f)
            except Exception as exc:
                print(f"[{i}/{len(lines)}] 合成失败，跳过：{exc}")
                continue
        else:
            dur = ffprobe_duration(f)
        slot = round(float(line["end_sec"]) - float(line["start_sec"]), 2)
        rows.append({**line, "file": str(f), "actual_sec": round(dur, 2), "slot_sec": slot,
                     "overflow": round(dur - slot, 2), "cached": hit})
        flag = "CACHE" if hit else "NEW"
        print(f"[{i}/{len(lines)}] {line['start_sec']:>6.1f}s 需 {slot:.1f}s 实 {dur:.2f}s "
              f"({flag}) {line['speaker']}: {line['text'][:16]}")

    if not rows:
        print("没有可合成的行")
        return 1

    # 按实际语音时长重排：计划的 start 能保住就保住，保不住就往后推
    gap = 0.12
    prev_end = 0.0
    for r in sorted(rows, key=lambda x: x["start_sec"]):
        wanted = float(r["start_sec"])
        actual_start = max(wanted, prev_end + (gap if prev_end else 0.0))
        r["shift"] = round(actual_start - wanted, 2)
        r["start_sec"] = round(actual_start, 3)
        prev_end = actual_start + float(r["actual_sec"])
    rows.sort(key=lambda x: x["start_sec"])
    total = float(plan.get("total_sec") or 0) or prev_end
    mix(rows, max(total, prev_end), Path(a.out))
    over = [r for r in rows if r["overflow"] > 0.3]
    report = {
        "book": plan.get("book_id"),
        "provider": a.provider,
        "lines": len(rows),
        "total_sec": total,
        "audio_out": a.out,
        "audio_duration_sec": round(ffprobe_duration(Path(a.out)), 2),
        "wall_sec": round(time.time() - t0, 1),
        "cached": sum(1 for r in rows if r["cached"]),
        "overflow_lines": [{"start_sec": r["start_sec"], "slot_sec": r["slot_sec"],
                            "actual_sec": r["actual_sec"], "overflow": r["overflow"],
                            "text": r["text"]} for r in over],
        "max_overflow": max((r["overflow"] for r in rows), default=0),
        "max_shift": max((r.get("shift", 0) for r in rows), default=0),
        "last_end_sec": round(prev_end, 2),
        "fits_total": prev_end <= total + 0.01,
    }
    Path(a.out).with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8")
    print(f"\n[done] {len(rows)} 行 / {report['wall_sec']}s 墙钟 / 缓存命中 {report['cached']} / "
          f"输出 {report['audio_duration_sec']}s -> {a.out}")
    if over:
        print(f"[warn] {len(over)} 行超出时段，最大 {report['max_overflow']:+.2f}s")
        for r in over[:5]:
            print(f"       {r['start_sec']:.1f}s 需 {r['slot_sec']}s 实 {r['actual_sec']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
