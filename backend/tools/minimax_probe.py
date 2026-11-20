# -*- coding: utf-8 -*-
"""MiniMax 探针：列出音色、测 TTS（非流式/流式）、测 ASR，可选测音色克隆。

安全：key 只从项目 .env / 环境变量读取，不打印完整值，不写进结果文件。

用法：
  # 1) 先把 key 写进 E:\\projects\\paperbook-backend\\.env
  #    MINIMAX_API_KEY=...
  python tools/minimax_probe.py
  python tools/minimax_probe.py --wav "data/books/cn01/queries/06.wav"
  python tools/minimax_probe.py --clone-ref "data/voice_refs/my_ref.wav" --clone-voice-id myvoice01
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.utils import wav_bytes  # noqa: E402


def mask(key: str) -> str:
    if not key:
        return "<empty>"
    return key[:6] + "***" + key[-4:] if len(key) > 12 else "***"


def hosts(configured: str) -> list[str]:
    out = []
    for h in [configured, "https://api.minimaxi.com", "https://api.minimax.io", "https://api.minimax.chat"]:
        h = (h or "").rstrip("/")
        if h and h not in out:
            out.append(h)
    return out


def url(host: str, path: str, group_id: str = "") -> str:
    u = host.rstrip("/") + path
    if group_id:
        u += ("&" if "?" in u else "?") + f"GroupId={group_id}"
    return u


def save_json(name: str, data) -> None:
    path = ROOT / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved:", path)


async def get_voices(client: httpx.AsyncClient, host: str) -> list[dict]:
    for body in ({"voice_type": "all"}, {"voice_type": "system"}):
        try:
            resp = await client.post(url(host, "/v1/get_voice", settings.minimax_group_id), json=body)
            obj = resp.json()
            base = obj.get("base_resp") or {}
            if resp.status_code == 200 and base.get("status_code") in (None, 0):
                voices: list[dict] = []
                for bucket in ("system_voice", "voice_cloning", "voice_generation", "voice_clone"):
                    for item in obj.get(bucket) or []:
                        voices.append({"bucket": bucket, **item})
                return voices
            print("get_voice:", resp.status_code, json.dumps(obj, ensure_ascii=False)[:400])
        except Exception as exc:  # noqa: BLE001
            print("get_voice failed:", type(exc).__name__, exc)
    return []


def pick_voice(voices: list[dict]) -> str:
    if settings.minimax_voice_id:
        return settings.minimax_voice_id
    preferred = ("female-shaonv", "female-tianmei", "female-yujie", "audiobook_female")
    for want in preferred:
        for v in voices:
            vid = str(v.get("voice_id") or "")
            if want in vid:
                return vid
    for v in voices:
        if v.get("bucket") == "system_voice" and v.get("voice_id"):
            return str(v["voice_id"])
    return ""


async def tts_test(client: httpx.AsyncClient, host: str, voice_id: str, text: str) -> None:
    common = {
        "model": settings.minimax_tts_model,
        "text": text,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": settings.minimax_speed,
            "vol": settings.minimax_vol,
            "pitch": settings.minimax_pitch,
        },
        "language_boost": settings.minimax_language_boost,
    }
    if settings.minimax_emotion:
        common["voice_setting"]["emotion"] = settings.minimax_emotion

    # 非流式 WAV
    body = dict(common)
    body.update({"stream": False, "audio_setting": {"sample_rate": 24000, "format": "wav", "channel": 1}})
    t0 = time.perf_counter()
    resp = await client.post(url(host, "/v1/t2a_v2", settings.minimax_group_id), json=body)
    obj = resp.json()
    base = obj.get("base_resp") or {}
    if resp.status_code != 200 or base.get("status_code") not in (None, 0):
        print("TTS non-stream FAIL:", resp.status_code, json.dumps(obj, ensure_ascii=False)[:500])
    else:
        audio_hex = (obj.get("data") or {}).get("audio") or ""
        raw = bytes.fromhex(audio_hex) if audio_hex else b""
        out = ROOT / "data" / "minimax_tts_nonstream.wav"
        out.write_bytes(raw)
        print(f"TTS non-stream OK: {time.perf_counter()-t0:.2f}s, {len(raw)} bytes -> {out}")

    # 流式 PCM
    body = dict(common)
    body.update(
        {
            "stream": True,
            "stream_options": {"exclude_aggregated_audio": True},
            "audio_setting": {"sample_rate": 24000, "format": "pcm", "channel": 1},
        }
    )
    t0 = time.perf_counter()
    first = None
    chunks: list[bytes] = []
    try:
        async with client.stream("POST", url(host, "/v1/t2a_v2", settings.minimax_group_id), json=body) as resp:
            if resp.status_code != 200:
                print("TTS stream FAIL:", resp.status_code, (await resp.aread())[:300])
                return
            async for line in resp.aiter_lines():
                line = (line or "").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                base = obj.get("base_resp") or {}
                if base.get("status_code") not in (None, 0):
                    print("TTS stream error:", base)
                    break
                data = obj.get("data") or {}
                hex_audio = data.get("audio")
                if hex_audio:
                    raw = bytes.fromhex(hex_audio)
                    if first is None:
                        first = (time.perf_counter() - t0) * 1000
                    chunks.append(raw)
                if data.get("status") == 2:
                    break
        pcm = b"".join(chunks)
        out = ROOT / "data" / "minimax_tts_stream.wav"
        out.write_bytes(wav_bytes(pcm, 24000))
        print(f"TTS stream OK: first_chunk={first:.0f}ms total={time.perf_counter()-t0:.2f}s audio={len(pcm)/2/24000:.2f}s -> {out}")
    except Exception as exc:  # noqa: BLE001
        print("TTS stream failed:", type(exc).__name__, exc)


async def asr_test(client: httpx.AsyncClient, host: str, wav_path: Path, language: str = "") -> None:
    if not wav_path.is_file():
        print("ASR skip: wav not found:", wav_path)
        return
    data = {"model": settings.minimax_asr_model, "response_format": "json"}
    headers = {"language": language or settings.minimax_asr_language} if (language or settings.minimax_asr_language) else {}
    files = {"file": (wav_path.name, wav_path.read_bytes(), "audio/wav")}
    t0 = time.perf_counter()
    resp = await client.post(url(host, "/v1/speech_to_text", settings.minimax_group_id), data=data, files=files, headers=headers)
    try:
        obj = resp.json()
    except Exception:
        obj = {"raw": resp.text[:500]}
    if resp.status_code == 200 and "text" in obj:
        print(f"ASR OK: {time.perf_counter()-t0:.2f}s text={obj.get('text')!r} duration={obj.get('duration')}")
        save_json("minimax_asr.json", obj)
    else:
        print("ASR FAIL:", resp.status_code, json.dumps(obj, ensure_ascii=False)[:600])
        print("hint: 401=key/region 不对；402=额度/套餐不含音频；404=host 不对；422=音频格式/敏感内容")


async def clone_test(client: httpx.AsyncClient, host: str, ref: Path, voice_id: str) -> None:
    if not ref.is_file():
        print("clone skip: ref not found:", ref)
        return
    files = {"file": (ref.name, ref.read_bytes(), "audio/wav")}
    resp = await client.post(url(host, "/v1/files/upload", settings.minimax_group_id), files=files, data={"purpose": "voice_clone"})
    obj = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text[:300]}
    file_id = ((obj.get("file") or {}).get("file_id")) or obj.get("file_id")
    print("upload:", resp.status_code, "file_id:", file_id, json.dumps(obj, ensure_ascii=False)[:400])
    if not file_id:
        return
    body = {
        "file_id": file_id,
        "voice_id": voice_id,
        "model": settings.minimax_tts_model,
        "text": "你好，我是你的新音色。",
    }
    resp = await client.post(url(host, "/v1/voice_clone", settings.minimax_group_id), json=body)
    print("voice_clone:", resp.status_code, resp.text[:600])


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", default=str(ROOT / "data" / "books" / "cn01" / "queries" / "06.wav"))
    parser.add_argument("--text", default="你好，我是纸书智伴的伴读老师，我们一起读绘本吧。")
    parser.add_argument("--clone-ref", default="")
    parser.add_argument("--clone-voice-id", default="paperbook_demo")
    args = parser.parse_args()

    key = settings.minimax_api_key
    print("MiniMax key:", mask(key), "host:", settings.minimax_host)
    if not key:
        print("MINIMAX_API_KEY 未设置。请把 key 写入 .env：")
        print("  E:/projects/paperbook-backend/.env")
        print("  MINIMAX_API_KEY=<your-key>")
        print("该文件已被 .gitignore 忽略；不要在聊天/仓库里贴完整 key。")
        return 2

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=8.0, read=180.0, write=90.0, pool=10.0),
        headers={"Authorization": f"Bearer {key}"},
    ) as client:
        working = ""
        voices: list[dict] = []
        for host in hosts(settings.minimax_host):
            print("try host:", host)
            voices = await get_voices(client, host)
            if voices:
                working = host
                break
        if voices:
            save_json("minimax_voices.json", voices)
            for v in voices[:20]:
                print("  voice:", v.get("voice_id"), "|", v.get("voice_name") or v.get("name") or "", "|", v.get("bucket"))
        else:
            # get_voice 可能受套餐限制；仍尝试 TTS/ASR
            working = settings.minimax_host.rstrip("/")
            print("get_voice 无结果，继续用", working, "试 TTS/ASR")
        voice_id = pick_voice(voices)
        print("selected voice:", voice_id or "<none>")
        if voice_id:
            await tts_test(client, working, voice_id, args.text)
        await asr_test(client, working, Path(args.wav))
        if args.clone_ref:
            await clone_test(client, working, Path(args.clone_ref), args.clone_voice_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
