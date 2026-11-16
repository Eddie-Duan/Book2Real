"""FastAPI 入口：REST + WebSocket 语音流 + 绘本/视频/会话/指标。"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .book.ingest import ingest_materials_zip, ingest_pdf, list_books
from .book.index import BookIndex
from .book.store import Store
from .book.video import VideoIndex
from .config import Settings, settings
from .pipeline.filler import FillerBank
from .pipeline.turn import VoiceTurnRunner
from .pipeline.vad import EnergyVAD
from .providers.base import ASRProvider, LLMProvider, TTSProvider
from .providers.factory import build_asr, build_llm, build_tts
from .utils import decode_audio_bytes, detect_lang, json_dumps, now_ms, resample_pcm16, wav_bytes

logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("paperbook")


class AppState:
    def __init__(self, cfg: Settings) -> None:
        self.settings = cfg
        cfg.ensure_dirs()
        self.store = Store(cfg)
        self.asr: ASRProvider = build_asr(cfg)
        self.tts: TTSProvider = build_tts(cfg)
        self.llm: LLMProvider = build_llm(cfg)
        self.filler = FillerBank(cfg)
        self.runner = VoiceTurnRunner(cfg, self.asr, self.tts, self.llm, self.filler, self.store)
        self.books: dict[str, BookIndex] = {}
        self.videos: dict[str, VideoIndex] = {}
        self.warm = False
        self.warming = False
        self.warmup_error = ""
        self.warmup_task: Optional[asyncio.Task] = None
        self.reload_books()

    def reload_books(self) -> None:
        self.books = {}
        for item in list_books(self.settings):
            try:
                path = self.settings.books_dir / item["id"] / "book.json"
                self.books[item["id"]] = BookIndex(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001
                log.warning("load book %s failed: %s", item.get("id"), exc)

    def book(self, book_id: str) -> BookIndex:
        if book_id not in self.books:
            self.reload_books()
        if book_id not in self.books:
            raise HTTPException(404, f"book not found: {book_id}")
        return self.books[book_id]

    def video(self, book_id: str) -> VideoIndex:
        if book_id not in self.videos:
            self.videos[book_id] = VideoIndex(self.settings, book_id)
        return self.videos[book_id]

    async def warmup(self) -> dict:
        if self.warming:
            return {"ok": False, "message": "warming"}
        self.warming = True
        errors = []
        t0 = time.perf_counter()
        try:
            try:
                await self.asr.warmup()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"asr: {exc}")
            try:
                await self.tts.warmup()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"tts: {exc}")
            try:
                await self.filler.prepare(self.tts)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"filler: {exc}")
            try:
                await self.llm.warmup()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"llm: {exc}")
            self.warm = True
            self.warmup_error = "; ".join(errors)
            return {"ok": not errors, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), "errors": errors, "filler": self.filler.status()}
        finally:
            self.warming = False


state: AppState | None = None


def get_state() -> AppState:
    if state is None:
        raise RuntimeError("app not initialized")
    return state


app = FastAPI(title="纸书智伴 · 语音后端", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    cfg = get_state().settings
    if cfg.api_token and not request.url.path.startswith(("/health", "/docs", "/openapi.json")):
        token = request.headers.get("X-API-Token") or request.query_params.get("token")
        if token != cfg.api_token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.on_event("startup")
async def on_startup() -> None:
    global state
    state = AppState(settings)
    log.info("books loaded: %s", list(state.books))
    if settings.warmup_on_start:
        state.warmup_task = asyncio.create_task(state.warmup())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if state:
        await state.asr.close()
        await state.tts.close()
        await state.llm.close()
        state.store.close()


# ── 健康 / 状态 ──────────────────────────────────────────────────────
@app.get("/health")
async def health():
    st = get_state()
    return {
        "ok": True,
        "warm": st.warm,
        "warming": st.warming,
        "warmup_error": st.warmup_error,
        "books": list(st.books),
        "asr": st.asr.status(),
        "tts": st.tts.status(),
        "llm": st.llm.status(),
        "filler": st.filler.status(),
        "latency_budget_ms": st.settings.latency_budget_ms,
    }


@app.get("/api/ready")
async def ready():
    st = get_state()
    return JSONResponse({"ready": st.warm, "warming": st.warming}, status_code=200 if st.warm else 503)


@app.post("/api/warmup")
async def warmup():
    return await get_state().warmup()


# ── 绘本 ────────────────────────────────────────────────────────────
@app.get("/api/books")
async def books():
    st = get_state()
    return {"books": list_books(st.settings)}


@app.get("/api/books/{book_id}")
async def book_detail(book_id: str):
    book = get_state().book(book_id).book
    return book


@app.get("/api/books/{book_id}/pages/{page_no}")
async def book_page(book_id: str, page_no: int):
    st = get_state()
    st.book(book_id)
    path = st.settings.books_dir / book_id / "pages" / f"{int(page_no):03d}.png"
    if not path.is_file():
        raise HTTPException(404, "page not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/books/{book_id}/queries/{name}")
async def book_query_audio(book_id: str, name: str):
    st = get_state()
    st.book(book_id)
    path = (st.settings.books_dir / book_id / "queries" / name).resolve()
    base = (st.settings.books_dir / book_id / "queries").resolve()
    if not str(path).startswith(str(base)) or not path.is_file():
        raise HTTPException(404, "query audio not found")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/books/{book_id}/elements")
async def book_elements(book_id: str, page: Optional[int] = None):
    book = get_state().book(book_id).book
    elements = book.get("elements", [])
    if page is not None:
        elements = [e for e in elements if int(e.get("page") or 0) == page]
    return {"elements": elements}


@app.post("/api/books/ingest")
async def ingest_upload(
    pdf: UploadFile = File(...),
    script: Optional[UploadFile] = File(None),
    book_id: str = Form(""),
    title: str = Form(""),
    lang: str = Form(""),
):
    st = get_state()
    st.settings.ensure_dirs()
    target = st.settings.source_dir / (pdf.filename or "book.pdf")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await pdf.read())
    script_path = None
    if script is not None:
        script_path = st.settings.source_dir / (script.filename or "script.txt")
        script_path.write_bytes(await script.read())
    book = await asyncio.to_thread(ingest_pdf, target, book_id or None, title or None, lang or None, script_path, st.settings, True)
    st.reload_books()
    return {"ok": True, "book": {"id": book["id"], "title": book["title"], "pages": book["page_count"], "summary": book.get("summary", {})}}


@app.post("/api/books/ingest-materials")
async def ingest_materials(zip_path: str = Form(...), force: bool = Form(False)):
    st = get_state()
    books = await asyncio.to_thread(ingest_materials_zip, zip_path, st.settings, force)
    st.reload_books()
    return {"ok": True, "books": [{"id": b["id"], "title": b["title"], "pages": b["page_count"]} for b in books]}


# ── ASR / TTS 单接口 ───────────────────────────────────────────────
@app.post("/api/asr")
async def asr_endpoint(audio: UploadFile = File(...), language: str = Form("")):
    st = get_state()
    data = await audio.read()
    pcm, rate = decode_audio_bytes(data, 16000)
    result = await st.asr.transcribe(pcm, rate, language=language or None)
    return {"text": result.text, "lang": result.lang, "duration_ms": round(result.duration_ms, 1)}


@app.post("/api/tts")
async def tts_endpoint(payload: dict):
    st = get_state()
    text = str(payload.get("text") or "").strip()
    lang = payload.get("lang") or detect_lang(text)
    if not text:
        raise HTTPException(400, "text is required")
    sr = getattr(st.tts, "sample_rate", st.settings.tts_sample_rate)

    async def gen():
        async for chunk in st.tts.stream(text, lang):
            yield chunk

    return StreamingResponse(gen(), media_type="application/x-pcm", headers={"X-Sample-Rate": str(sr), "X-Audio-Format": "pcm16le", "Cache-Control": "no-store"})


# ── 核心问答（HTTP 非流式 / NDJSON 流式）────────────────────────────
async def _run_http_turn(
    st: AppState,
    book_id: str,
    *,
    text: str = "",
    audio_bytes: bytes = b"",
    session_id: str = "http",
    lang: str = "",
    page: Optional[int] = None,
    element: Optional[dict] = None,
    warmup: bool = False,
):
    book = st.book(book_id)
    speech_end = now_ms()
    if audio_bytes:
        pcm, rate = decode_audio_bytes(audio_bytes, 16000)
        async for event in st.runner.run_voice(
            session_id, book, pcm, rate, language=lang or None, current_page=page, current_element=element, speech_end_ms=speech_end, warmup=warmup, video_index=st.video(book_id) if st.video(book_id).ready else None
        ):
            yield event
    else:
        async for event in st.runner.run_text(
            session_id, book, text, language=lang or None, current_page=page, current_element=element, speech_end_ms=speech_end, warmup=warmup, video_index=st.video(book_id) if st.video(book_id).ready else None
        ):
            yield event


@app.post("/api/ask")
async def ask(request: Request):
    st = get_state()
    content_type = request.headers.get("content-type", "")
    audio_bytes = b""
    payload: dict = {}
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        book_id = str(form.get("book_id") or "")
        session_id = str(form.get("session_id") or "http")
        lang = str(form.get("lang") or "")
        page = int(form["page"]) if form.get("page") else None
        element = json.loads(form["element"]) if form.get("element") else None
        upload = form.get("audio")
        if upload is not None:
            audio_bytes = await upload.read()
        text = str(form.get("text") or "")
    else:
        payload = await request.json()
        book_id = str(payload.get("book_id") or "")
        session_id = str(payload.get("session_id") or "http")
        lang = str(payload.get("lang") or "")
        page = int(payload["page"]) if payload.get("page") is not None else None
        element = payload.get("element")
        text = str(payload.get("text") or "")
        if payload.get("audio_b64"):
            audio_bytes = base64.b64decode(payload["audio_b64"])
    if not book_id:
        raise HTTPException(400, "book_id is required")
    st.book(book_id)
    answer = ""
    action = None
    evidence = []
    lang_out = lang or "zh"
    timings = {}
    metrics = {}
    asr_text = ""
    asr_lang = ""
    audio_parts: list[bytes] = []
    sample_rate = getattr(st.tts, "sample_rate", st.settings.tts_sample_rate)
    async for event in _run_http_turn(st, book_id, text=text, audio_bytes=audio_bytes, session_id=session_id, lang=lang, page=page, element=element):
        if event["type"] == "asr_final":
            asr_text = event.get("text", "")
            asr_lang = event.get("lang", "")
        elif event["type"] == "answer":
            answer = event.get("text", "")
            action = event.get("action")
            evidence = event.get("evidence", [])
            lang_out = event.get("lang", lang_out)
            timings = event.get("timings", {})
            metrics = event.get("metrics", {})
        elif event["type"] == "audio":
            audio_parts.append(event.get("pcm", b""))
        elif event["type"] == "audio_start":
            sample_rate = event.get("sample_rate", sample_rate)
        elif event["type"] == "error":
            raise HTTPException(500, event.get("message", "pipeline error"))
    pcm = b"".join(audio_parts)
    return {
        "answer": answer,
        "lang": lang_out,
        "asr_text": asr_text,
        "asr_lang": asr_lang,
        "action": action,
        "evidence": evidence,
        "timings": timings,
        "metrics": metrics,
        "audio_format": "pcm16",
        "sample_rate": sample_rate,
        "audio_b64": base64.b64encode(pcm).decode("ascii") if pcm else "",
        "audio_wav_b64": base64.b64encode(wav_bytes(pcm, sample_rate)).decode("ascii") if pcm else "",
    }


@app.post("/api/ask/stream")
async def ask_stream(request: Request):
    st = get_state()
    payload = await request.json()
    book_id = str(payload.get("book_id") or "")
    if not book_id:
        raise HTTPException(400, "book_id is required")
    st.book(book_id)
    session_id = str(payload.get("session_id") or "http-stream")
    lang = str(payload.get("lang") or "")
    page = int(payload["page"]) if payload.get("page") is not None else None
    element = payload.get("element")
    text = str(payload.get("text") or "")
    audio_bytes = base64.b64decode(payload["audio_b64"]) if payload.get("audio_b64") else b""

    async def gen():
        async for event in _run_http_turn(st, book_id, text=text, audio_bytes=audio_bytes, session_id=session_id, lang=lang, page=page, element=element):
            out = dict(event)
            if out["type"] == "audio" and out.get("pcm"):
                out["audio_b64"] = base64.b64encode(out.pop("pcm")).decode("ascii")
            elif "pcm" in out:
                out.pop("pcm", None)
            yield (json_dumps(out) + "\n").encode("utf-8")

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


# ── WebSocket 语音流 ───────────────────────────────────────────────
@app.websocket("/ws/voice/{session_id}")
async def voice_ws(ws: WebSocket, session_id: str):
    await ws.accept()
    st = get_state()
    book_id = ""
    book: Optional[BookIndex] = None
    sample_rate = 16000
    lang = ""
    page: Optional[int] = None
    element: Optional[dict] = None
    warmup = ws.query_params.get("warmup") == "1"
    vad_mode = "both"  # both | client | server
    vad = EnergyVAD(sample_rate=sample_rate, frame_ms=st.settings.vad_frame_ms, silence_ms=st.settings.vad_silence_ms, min_speech_ms=st.settings.vad_min_speech_ms, energy_ratio=st.settings.vad_energy_ratio, min_rms=st.settings.vad_min_rms)
    buffer = bytearray()
    speaking = False
    turn_task: Optional[asyncio.Task] = None
    partial_task: Optional[asyncio.Task] = None
    turn_active = False

    async def send_event(event: dict) -> None:
        if event.get("type") == "audio" and event.get("pcm"):
            await ws.send_bytes(event["pcm"])
        else:
            out = {k: v for k, v in event.items() if k != "pcm"}
            await ws.send_json(out)

    async def partial_loop() -> None:
        nonlocal speaking
        while speaking:
            await asyncio.sleep(st.settings.asr_partial_ms / 1000.0)
            if not speaking or len(buffer) < 6400:
                continue
            window = int(st.settings.asr_partial_window_s * sample_rate) * 2
            data = bytes(buffer[-window:])
            try:
                result = await st.asr.transcribe(data, sample_rate, language=lang or None, partial=True)
                if result.text and not turn_active:
                    await ws.send_json({"type": "asr_partial", "text": result.text, "lang": result.lang})
            except Exception:
                continue

    async def end_turn(speech_end_override: Optional[float] = None) -> None:
        nonlocal turn_active, speaking, buffer, partial_task
        if turn_active or book is None:
            return
        turn_active = True
        speaking = False
        if partial_task:
            partial_task.cancel()
            partial_task = None
        if not buffer:
            turn_active = False
            return
        data = bytes(buffer)
        buffer = bytearray()
        speech_end = speech_end_override or now_ms()
        await ws.send_json({"type": "speech_end", "t": speech_end, "bytes": len(data)})
        try:
            async for event in st.runner.run_voice(
                session_id, book, data, sample_rate, language=lang or None, current_page=page, current_element=element, speech_end_ms=speech_end, warmup=warmup, video_index=st.video(book_id) if st.video(book_id).ready else None
            ):
                await send_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed")
            await ws.send_json({"type": "error", "message": str(exc)})
        finally:
            turn_active = False
            vad.reset()

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                chunk = message["bytes"]
                if not turn_active:
                    buffer.extend(chunk)
                    events = vad.push(chunk)
                    for ev in events:
                        if ev.kind == "speech_start" and not speaking:
                            speaking = True
                            buffer = bytearray(buffer[-int(sample_rate * 0.4) * 2 :]) if buffer else buffer
                            await ws.send_json({"type": "speech_start", "t": now_ms()})
                            if st.settings.asr_partial_ms > 0:
                                partial_task = asyncio.create_task(partial_loop())
                        elif ev.kind == "speech_end" and vad_mode != "client":
                            tail_ms = max(0.0, float(ev.time_ms) - float(ev.last_speech_ms))
                            await end_turn(now_ms() - tail_ms)
                continue
            if message.get("text") is None:
                continue
            try:
                msg = json.loads(message["text"])
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "bad json"})
                continue
            kind = msg.get("type")
            if kind == "start":
                book_id = str(msg.get("book_id") or book_id)
                book = st.book(book_id)
                sample_rate = int(msg.get("sample_rate") or 16000)
                vad = EnergyVAD(sample_rate=sample_rate, frame_ms=st.settings.vad_frame_ms, silence_ms=st.settings.vad_silence_ms, min_speech_ms=st.settings.vad_min_speech_ms, energy_ratio=st.settings.vad_energy_ratio, min_rms=st.settings.vad_min_rms)
                lang = str(msg.get("lang") or "")
                page = int(msg["page"]) if msg.get("page") is not None else None
                element = msg.get("element")
                warmup = bool(msg.get("warmup", warmup))
                vad_mode = str(msg.get("vad") or "both")
                st.store.ensure_session(session_id, book_id)
                await ws.send_json({"type": "ready", "book_id": book_id, "sample_rate": sample_rate, "warm": st.warm, "warmup": warmup, "vad": vad_mode})
            elif kind == "vad":
                event = msg.get("event")
                if event == "speech_start" and not speaking and not turn_active:
                    speaking = True
                    await ws.send_json({"type": "speech_start", "t": now_ms()})
                    if st.settings.asr_partial_ms > 0:
                        partial_task = asyncio.create_task(partial_loop())
                elif event == "speech_end" and speaking and not turn_active:
                    await end_turn(msg.get("t") or now_ms())
            elif kind == "text":
                text = str(msg.get("text") or "").strip()
                if text and book and not turn_active:
                    turn_active = True
                    speech_end = msg.get("t") or now_ms()
                    try:
                        async for event in st.runner.run_text(
                            session_id, book, text, language=lang or None, current_page=page, current_element=element, speech_end_ms=speech_end, warmup=warmup, video_index=st.video(book_id) if st.video(book_id).ready else None
                        ):
                            await send_event(event)
                    finally:
                        turn_active = False
            elif kind == "barge_in":
                if partial_task:
                    partial_task.cancel()
                    partial_task = None
                speaking = False
                turn_active = False
                buffer = bytearray()
                vad.reset()
                await ws.send_json({"type": "barge_in_ack"})
            elif kind == "end":
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.exception("ws failed")
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if partial_task:
            partial_task.cancel()
        if turn_task:
            turn_task.cancel()


# ── 会话历史 ───────────────────────────────────────────────────────
@app.get("/api/sessions/{session_id}/history")
async def session_history(session_id: str, limit: int = 50):
    return {"messages": get_state().store.history(session_id, limit)}


@app.post("/api/sessions/{session_id}/events")
async def session_event(session_id: str, payload: dict):
    st = get_state()
    st.store.add_message(
        session_id,
        str(payload.get("role") or "event"),
        str(payload.get("text") or ""),
        str(payload.get("lang") or ""),
        int(payload["page"]) if payload.get("page") is not None else None,
        float(payload["t"]) if payload.get("t") is not None else None,
        str(payload.get("element") or ""),
        str(payload.get("image") or ""),
        payload.get("action"),
    )
    return {"ok": True}


# ── 指标 ───────────────────────────────────────────────────────────
@app.get("/api/metrics/latency")
async def metrics_latency(session_id: str = "", book_id: str = ""):
    st = get_state()
    return st.store.latency_stats(st.settings.latency_budget_ms, session_id, book_id)


@app.get("/api/metrics/turns")
async def metrics_turns(limit: int = 20):
    return {"turns": get_state().store.recent_turns(limit)}


# ── 视频索引 ───────────────────────────────────────────────────────
@app.post("/api/video/{book_id}/index")
async def video_index(book_id: str, video: UploadFile = File(...), manifest: Optional[UploadFile] = File(None)):
    st = get_state()
    book = st.book(book_id)
    st.settings.ensure_dirs()
    video_dir = st.settings.books_dir / book_id / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    video_path = video_dir / (video.filename or "video.mp4")
    video_path.write_bytes(await video.read())
    manifest_path = None
    if manifest is not None:
        manifest_path = video_dir / "manifest.json"
        manifest_path.write_bytes(await manifest.read())
    idx = await asyncio.to_thread(st.video(book_id).build, video_path, manifest_path=manifest_path, book=book.book, force=True)
    return {"ok": True, "status": st.video(book_id).status(), "duration": idx.get("duration"), "frames": len(idx.get("frames", []))}


@app.post("/api/video/{book_id}/index-path")
async def video_index_path(book_id: str, payload: dict):
    st = get_state()
    book = st.book(book_id)
    video_path = payload.get("video_path")
    if not video_path or not Path(video_path).is_file():
        raise HTTPException(400, "video_path not found")
    idx = await asyncio.to_thread(st.video(book_id).build, video_path, manifest_path=payload.get("manifest_path"), book=book.book, force=True)
    return {"ok": True, "status": st.video(book_id).status(), "duration": idx.get("duration"), "frames": len(idx.get("frames", []))}


@app.get("/api/video/{book_id}/status")
async def video_status(book_id: str):
    return get_state().video(book_id).status()


@app.get("/api/video/{book_id}/frames/{name}")
async def video_frame(book_id: str, name: str):
    st = get_state()
    path = (st.settings.books_dir / book_id / "video" / "frames" / name).resolve()
    base = (st.settings.books_dir / book_id / "video" / "frames").resolve()
    if not str(path).startswith(str(base)) or not path.is_file():
        raise HTTPException(404, "frame not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/video/{book_id}/search")
async def video_search(book_id: str, q: str, limit: int = 20):
    return {"hits": get_state().video(book_id).search_text(q, limit)}


@app.get("/api/video/{book_id}/frame")
async def video_frame_at(book_id: str, t: float):
    return get_state().video(book_id).frame_at(t) or {}


@app.get("/api/video/{book_id}/describe")
async def video_describe(book_id: str, t: float, box: str = ""):
    st = get_state()
    parsed_box = [float(x) for x in box.split(",")] if box else None
    return st.video(book_id).describe(t, parsed_box, st.book(book_id).book)


@app.get("/api/video/{book_id}/elements")
async def video_elements(book_id: str, t: float):
    st = get_state()
    return {"elements": st.video(book_id).elements_at(t, st.book(book_id).book)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False, log_level=settings.log_level.lower())
