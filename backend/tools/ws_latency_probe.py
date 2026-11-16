# -*- coding: utf-8 -*-
"""WebSocket 端到端首音探针：模拟麦克风把 wav 分帧推给后端，统计
speech_end → 第一块音频 的真实延迟。

用法：
  python tools/ws_latency_probe.py --book cn01 --wav "<Query wav路径>/06.wav"
  python tools/ws_latency_probe.py --book cn01 --text "他们在搭什么？"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave

import websockets


def load_pcm(path: str, target_rate: int = 16000) -> tuple[bytes, int]:
    with wave.open(path, "rb") as wf:
        assert wf.getsampwidth() == 2, "need PCM16 wav"
        rate = wf.getframerate()
        ch = wf.getnchannels()
        data = wf.readframes(wf.getnframes())
    if ch > 1:
        import numpy as np

        arr = np.frombuffer(data, dtype="<i2").reshape(-1, ch)
        data = arr[:, 0].astype("<i2").tobytes()
    if rate != target_rate:
        import numpy as np

        samples = np.frombuffer(data, dtype="<i2").astype("float32") / 32768.0
        n = int(len(samples) * target_rate / rate)
        x_old = np.linspace(0, 1, len(samples), endpoint=False)
        x_new = np.linspace(0, 1, n, endpoint=False)
        data = (np.interp(x_new, x_old, samples) * 32767).astype("<i2").tobytes()
        rate = target_rate
    return data, rate


async def run(uri: str, book: str, wav: str = "", text: str = "", page: int | None = None, warmup: bool = False) -> None:
    events: list[dict] = []
    speech_end_t: float | None = None
    first_audio_wall: float | None = None
    first_audio_after: float | None = None
    answer = ""
    async with websockets.connect(uri, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "start", "book_id": book, "sample_rate": 16000, "page": page, "warmup": warmup, "vad": "both"}))
        ready = json.loads(await ws.recv())
        print("ready:", ready)
        if text:
            await ws.send(json.dumps({"type": "text", "text": text, "t": time.time() * 1000}))
        else:
            pcm, rate = load_pcm(wav, 16000)
            frame = int(rate * 0.02) * 2
            for i in range(0, len(pcm), frame):
                await ws.send(pcm[i : i + frame])
                await asyncio.sleep(0.02)
            # 300ms 静音，触发服务端 VAD
            silence = b"\x00\x00" * int(rate * 0.02)
            for _ in range(15):
                await ws.send(silence)
                await asyncio.sleep(0.02)
            await ws.send(json.dumps({"type": "vad", "event": "speech_end"}))

        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                print("timeout waiting for event")
                break
            if isinstance(msg, bytes):
                now = time.time() * 1000
                if first_audio_wall is None:
                    first_audio_wall = now
                    if speech_end_t is not None:
                        first_audio_after = now - speech_end_t
                    print(f"FIRST_AUDIO bytes={len(msg)} after_speech_end={first_audio_after and round(first_audio_after,1)}ms")
                continue
            event = json.loads(msg)
            events.append(event)
            kind = event.get("type")
            if kind == "speech_end":
                speech_end_t = event.get("t")
                print("speech_end:", speech_end_t)
            elif kind == "asr_final":
                print("asr_final:", event.get("text"), event.get("ms"), "ms")
            elif kind == "answer_delta":
                answer += event.get("text", "")
            elif kind == "answer":
                answer = event.get("text", answer)
                print("answer:", answer)
                print("action:", json.dumps(event.get("action"), ensure_ascii=False))
                print("timings:", json.dumps(event.get("timings"), ensure_ascii=False))
                print("metrics:", json.dumps(event.get("metrics"), ensure_ascii=False))
            elif kind in ("done", "error", "tts_error", "filler_used", "barge_in_ack"):
                print(kind + ":", json.dumps({k: v for k, v in event.items() if k != "pcm"}, ensure_ascii=False)[:500])
                if kind in ("done", "error"):
                    break
    print("answer_len:", len(answer))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8790/ws/voice/latency-probe")
    parser.add_argument("--book", required=True)
    parser.add_argument("--wav", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--page", type=int, default=None)
    parser.add_argument("--warmup", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.book, args.wav, args.text, args.page, args.warmup))


if __name__ == "__main__":
    main()
