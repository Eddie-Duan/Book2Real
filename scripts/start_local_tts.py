# -*- coding: utf-8 -*-
"""本地流式 TTS 服务（默认 9890）。

引擎：
  it25      IndexTTS-2.5（整句合成后按 20ms 分块返回；质量好、RTF 约 0.8）
  cosyvoice CosyVoice3+LoRA（模型内部 stream=True，首块更早但 RTF 约 1.0）

运行（必须用对应 conda 环境）：
  <你的 cosyvoice 环境>\\python.exe scripts\\start_local_tts.py --engine cosyvoice
  <你的 indextts25 环境>\\python.exe scripts\\start_local_tts.py --engine it25

环境变量：
  COSYVOICE_HOME   CosyVoice 仓库目录（含 server_api_v2.py）
  IT25_HOME        index-tts 仓库目录（含 server_api_v2.py）
  TTS_REF_AUDIO    参考音频（默认 ./data/voice_refs/default.wav）
  TTS_REF_PROMPT   参考音频转写

接口：
  GET  /health
  POST /tts_stream  {text, ref_audio_path?, prompt_text?, speed_factor?, lang?}
       -> chunked raw PCM16LE mono; headers X-Sample-Rate / X-Audio-Format / X-Engine
  POST /tts         -> WAV（兼容已有 shim）
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

DEFAULT_REF = os.path.expanduser(
    os.environ.get(
        "TTS_REF_AUDIO",
        str(Path(__file__).resolve().parent.parent / "data" / "voice_refs" / "default.wav"),
    )
)
DEFAULT_PROMPT = os.environ.get("TTS_REF_PROMPT", "这是一段用于音色克隆的参考音频转写。")
ENGINE = "it25"
MODEL_READY = False
SAMPLE_RATE = 24000
INFER_LOCK = threading.Lock()
ENGINE_OBJ = None
FIRST_CHUNK_MS = None


def log(msg: str) -> None:
    print(f"[local-tts] {msg}", flush=True)


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _engine_module_path(engine: str) -> Path:
    if engine == "cosyvoice":
        home = os.environ.get("COSYVOICE_HOME", "")
        hint = "COSYVOICE_HOME"
    else:
        home = os.environ.get("IT25_HOME", "")
        hint = "IT25_HOME"
    if not home:
        raise RuntimeError(f"set {hint} to the TTS repository directory (contains server_api_v2.py)")
    path = Path(home) / "server_api_v2.py"
    if not path.is_file():
        raise RuntimeError(f"{path} not found; check {hint}")
    return path


def load_engine(engine: str):
    global ENGINE_OBJ, SAMPLE_RATE, MODEL_READY
    t0 = time.time()
    module_path = _engine_module_path(engine)
    if engine == "cosyvoice":
        mod = _load_module(str(module_path), "cosy_shim")
        mod.load_model()
        ENGINE_OBJ = mod
        SAMPLE_RATE = int(mod.cosyvoice.sample_rate)
    else:
        mod = _load_module(str(module_path), "it25_shim")
        mod.load_model()
        ENGINE_OBJ = mod
        SAMPLE_RATE = 24000
    MODEL_READY = True
    log(f"engine={engine} ready in {time.time()-t0:.1f}s sample_rate={SAMPLE_RATE}")


def _float_to_pcm(samples: np.ndarray) -> bytes:
    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()


def synth_it25(text: str, ref: str, prompt: str, speed: float, lang: str):
    model = ENGINE_OBJ.model
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        model.infer(
            spk_audio_prompt=ref,
            text=text,
            output_path=tmp.name,
            lang=lang or "zh",
            emo_vector=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.35],
            verbose=False,
        )
        with wave.open(tmp.name, "rb") as wf:
            rate = wf.getframerate()
            data = wf.readframes(wf.getnframes())
            if wf.getnchannels() > 1:
                arr = np.frombuffer(data, dtype="<i2").reshape(-1, wf.getnchannels())
                data = arr[:, 0].astype("<i2").tobytes()
        return data, rate
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def synth_cosy(text: str, ref: str, prompt: str, speed: float, lang: str):
    cosy = ENGINE_OBJ.cosyvoice
    prompt_full = "You are a helpful assistant.<|endofprompt|>" + (prompt or "")
    for out in cosy.inference_zero_shot(
        tts_text=text,
        prompt_text=prompt_full,
        prompt_wav=ref,
        stream=True,
        speed=speed or 1.0,
    ):
        speech = out.get("tts_speech")
        if speech is None:
            continue
        arr = speech.squeeze(0).detach().cpu().numpy()
        yield _float_to_pcm(arr)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log(self.address_string() + " " + (fmt % args))

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlsplit(self.path).path == "/health":
            self._json(200, {"ok": MODEL_READY, "engine": ENGINE, "sample_rate": SAMPLE_RATE, "first_chunk_ms": FIRST_CHUNK_MS})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ("/tts_stream", "/tts"):
            self._json(404, {"error": "not found"})
            return
        if not MODEL_READY:
            self._json(503, {"error": "model still loading"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": f"bad json: {exc}"})
            return
        text = str(payload.get("text") or "").strip()
        ref = str(payload.get("ref_audio_path") or DEFAULT_REF).strip()
        prompt = str(payload.get("prompt_text") or DEFAULT_PROMPT).strip()
        speed = float(payload.get("speed_factor") or 1.0)
        lang = str(payload.get("lang") or "zh")
        if not text:
            self._json(400, {"error": "text is required"})
            return
        if not os.path.isfile(ref):
            self._json(400, {"error": f"ref_audio_path not found: {ref}"})
            return
        # 极短文本给声码器垫一句，避免卷积核越界
        if len(text) < 6:
            text = "好的，" + text
        t0 = time.time()
        try:
            with INFER_LOCK:
                if ENGINE == "cosyvoice":
                    gen = synth_cosy(text, ref, prompt, speed, lang)
                    first = next(gen, None)
                    chunks = ([] if first is None else [first]) + list(gen)
                    rate = SAMPLE_RATE
                else:
                    data, rate = synth_it25(text, ref, prompt, speed, lang)
                    chunks = [data[i : i + int(rate * 0.02) * 2] for i in range(0, len(data), int(rate * 0.02) * 2)]
            if not chunks:
                self._json(500, {"error": "empty audio"})
                return
            if path == "/tts":
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(rate)
                    wf.writeframes(b"".join(chunks))
                body = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                log(f"tts batch {len(text)}ch -> {sum(len(c) for c in chunks)/2/rate:.2f}s in {time.time()-t0:.2f}s")
                return
            global FIRST_CHUNK_MS
            self.send_response(200)
            self.send_header("Content-Type", "application/x-pcm")
            self.send_header("X-Sample-Rate", str(rate))
            self.send_header("X-Audio-Format", "pcm16le")
            self.send_header("X-Engine", ENGINE)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for idx, chunk in enumerate(chunks):
                if not chunk:
                    continue
                if idx == 0:
                    FIRST_CHUNK_MS = round((time.time() - t0) * 1000, 1)
                    log(f"first chunk {FIRST_CHUNK_MS}ms for {text!r}")
                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            log(f"tts stream {len(text)}ch -> {sum(len(c) for c in chunks)/2/rate:.2f}s in {time.time()-t0:.2f}s")
        except Exception as exc:  # noqa: BLE001
            log(f"infer error: {type(exc).__name__}: {exc}")
            try:
                self._json(500, {"error": str(exc)})
            except Exception:
                pass


def main() -> int:
    global ENGINE
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", default=os.environ.get("LOCAL_TTS_ENGINE", "it25"), choices=["it25", "cosyvoice"])
    parser.add_argument("--port", type=int, default=int(os.environ.get("LOCAL_TTS_PORT", "9890")))
    parser.add_argument("--host", default=os.environ.get("LOCAL_TTS_HOST", "127.0.0.1"))
    args = parser.parse_args()
    ENGINE = args.engine
    log(f"loading engine={ENGINE} ...")
    load_engine(ENGINE)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    log(f"listening http://{args.host}:{args.port} (engine={ENGINE})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
