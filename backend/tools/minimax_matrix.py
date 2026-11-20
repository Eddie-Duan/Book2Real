# -*- coding: utf-8 -*-
"""MiniMax TTS 参数小矩阵：模型 / 音色 / 情绪 / 语速，输出试听 wav + 延迟表。"""
from __future__ import annotations

import asyncio, json, sys, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import settings  # noqa: E402
from app.utils import wav_bytes  # noqa: E402

TEXT = "小满和花花一起搭积木城堡，她们玩得真开心。"
OUT = ROOT / "data" / "minimax_matrix"
COMBOS = [
    ("speech-2.6-turbo", "female-shaonv", "calm", 1.0),
    ("speech-2.6-turbo", "lovely_girl", "calm", 1.0),
    ("speech-2.6-turbo", "cute_boy", "calm", 1.0),
    ("speech-2.6-turbo", "female-tianmei", "calm", 1.0),
    ("speech-2.6-turbo", "Chinese (Mandarin)_Warm_Girl", "calm", 1.0),
    ("speech-2.6-turbo", "Chinese (Mandarin)_Crisp_Girl", "calm", 1.0),
    ("speech-2.6-turbo", "female-shaonv", "happy", 1.0),
    ("speech-2.6-turbo", "female-shaonv", "calm", 0.9),
    ("speech-2.8-turbo", "female-shaonv", "calm", 1.0),
    ("speech-2.8-turbo", "lovely_girl", "calm", 1.0),
    ("speech-2.8-hd", "female-shaonv", "calm", 1.0),
    ("speech-2.8-turbo", "Chinese (Mandarin)_Warm_Girl", "calm", 0.9),
]

async def one(client, model, voice, emotion, speed):
    payload = {
        "model": model, "text": TEXT, "stream": True,
        "stream_options": {"exclude_aggregated_audio": True},
        "voice_setting": {"voice_id": voice, "speed": speed, "vol": 1.0, "pitch": 0, "emotion": emotion},
        "audio_setting": {"sample_rate": 24000, "format": "pcm", "channel": 1},
        "language_boost": "Chinese",
    }
    url = settings.minimax_host.rstrip("/") + "/v1/t2a_v2"
    t0 = time.perf_counter(); first = None; chunks = []; err = ""
    try:
        async with client.stream("POST", url, json=payload) as resp:
            if resp.status_code != 200:
                return {"model": model, "voice": voice, "emotion": emotion, "speed": speed, "error": f"HTTP {resp.status_code}: {(await resp.aread())[:200]!r}"}
            async for line in resp.aiter_lines():
                line = (line or "").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try: obj = json.loads(line)
                except json.JSONDecodeError: continue
                base = obj.get("base_resp") or {}
                if base.get("status_code") not in (None, 0):
                    return {"model": model, "voice": voice, "emotion": emotion, "speed": speed, "error": f"{base.get('status_code')}: {base.get('status_msg')}"}
                data = obj.get("data") or {}
                if data.get("audio"):
                    if first is None: first = (time.perf_counter() - t0) * 1000
                    chunks.append(bytes.fromhex(data["audio"]))
                if data.get("status") == 2:
                    break
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    pcm = b"".join(chunks)
    if not pcm:
        return {"model": model, "voice": voice, "emotion": emotion, "speed": speed, "error": err or "empty audio"}
    OUT.mkdir(parents=True, exist_ok=True)
    safe = voice.replace(" ", "_").replace("(", "").replace(")", "").replace("_Mandarin", "")
    path = OUT / f"{model}_{safe}_{emotion}_{speed}.wav"
    path.write_bytes(wav_bytes(pcm, 24000))
    return {"model": model, "voice": voice, "emotion": emotion, "speed": speed,
            "first_chunk_ms": round(first or 0, 1), "total_ms": round((time.perf_counter()-t0)*1000, 1),
            "audio_s": round(len(pcm)/2/24000, 2), "file": str(path.relative_to(ROOT))}

async def main():
    if not settings.minimax_api_key:
        print("MINIMAX_API_KEY is empty")
        return 2
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=8, read=180, write=90, pool=10), headers={"Authorization": f"Bearer {settings.minimax_api_key}"}) as c:
        rows = []
        for combo in COMBOS:
            r = await one(c, *combo)
            rows.append(r)
            print(json.dumps(r, ensure_ascii=False))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", OUT / "summary.json")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
