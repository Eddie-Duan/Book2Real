"""融合层：把既有阅读器/影院前端（book2real/web）接到 FastAPI 上。

后端是唯一服务：这里只做**静态资源托管 + 旧路由适配**，业务仍由 backend 的
providers/pipeline/book 提供。路由与 ``book2real/reader_app.py`` 的旧 HTTP 层
1:1（页面、/api/book、/api/film、/api/job、/api/import、/api/video、/api/tts 兼容），
方便前端在单进程下照旧工作。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .utils import detect_lang, wav_bytes

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "book2real" / "web"
BOOKS = REPO / "books"
RUNS = REPO / "runs"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

try:  # 既有前端/编排逻辑复用，避免两套 film/import 代码
    from book2real import reader_app as legacy
except Exception as _exc:  # pragma: no cover - repo layout is expected
    legacy = None
    _legacy_error = _exc

CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'"
)
HTML_HEADERS = {
    "Content-Security-Policy": CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}
STATIC_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}

router = APIRouter(tags=["frontend"])


# ------------------------------------------------------------------ helpers
def _within(base: Path, rel: str) -> Path | None:
    try:
        base_r = base.resolve()
        target = (base / rel).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return target if target.is_relative_to(base_r) else None


def _html(name: str) -> FileResponse:
    path = WEB / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="text/html; charset=utf-8", headers=HTML_HEADERS)


def _static(base: Path, rel: str, extra: dict | None = None) -> FileResponse:
    target = _within(base, rel)
    if target is None:
        raise HTTPException(403, "forbidden")
    if not target.is_file():
        raise HTTPException(404, "not found")
    headers = dict(STATIC_HEADERS)
    if extra:
        headers.update(extra)
    if target.suffix.lower() in (".html", ".htm"):
        headers.update(HTML_HEADERS)
    return FileResponse(target, headers=headers)


def _legacy() -> Any:
    if legacy is None:  # pragma: no cover - only when the reader package is absent
        raise HTTPException(500, f"reader frontend unavailable: {_legacy_error}")
    return legacy


def _book_json(book_id: str) -> Path | None:
    bid = (book_id or "").strip().strip("/")
    if not bid or "/" in bid or "\\" in bid or ".." in bid:
        return None
    target = _within(BOOKS, f"{bid}/reader/book.json")
    return target if target and target.is_file() else None


# ------------------------------------------------------------------ pages
@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return _html("reader.html")


@router.get("/film", include_in_schema=False)
async def film_page() -> FileResponse:
    return _html("film.html")


@router.get("/static/{rel:path}", include_in_schema=False)
async def static(rel: str) -> FileResponse:
    extra = {"Service-Worker-Allowed": "/"} if rel.endswith("sw.js") else None
    return _static(WEB, rel, extra)


@router.get("/books/{rel:path}", include_in_schema=False)
async def books_static(rel: str) -> FileResponse:
    return _static(BOOKS, rel)


@router.get("/media/{rel:path}", include_in_schema=False)
async def media_static(rel: str) -> FileResponse:
    return _static(RUNS, rel)


# ------------------------------------------------------------------ reader data
@router.get("/api/book")
async def book_detail_for_reader(book: str = "") -> JSONResponse:
    path = _book_json(book)
    if path is None:
        raise HTTPException(404, "book not found")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@router.get("/api/books/{book_id}/files")
async def book_files(book_id: str) -> JSONResponse:
    code, payload = _legacy()._book_files(book_id)
    return JSONResponse(payload, status_code=code)


# ------------------------------------------------------------------ film
@router.get("/api/film/{book_id}")
async def film_timeline(book_id: str) -> JSONResponse:
    code, payload = _legacy().film_timeline(book_id)
    return JSONResponse(payload, status_code=code)


@router.get("/api/film/{book_id}/video")
async def film_video(book_id: str) -> FileResponse:
    target = _legacy().film_video(book_id)
    if target is None:
        raise HTTPException(404, "film not found")
    # Starlette FileResponse 自带 Range/206，可拖动时间轴
    return FileResponse(target, media_type="video/mp4", headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ jobs（导入/成片沿用旧编排）
@router.get("/api/job")
async def job(id: str = "") -> JSONResponse:
    j = _legacy().JOBS.get(id)
    return JSONResponse(j or {"error": "unknown job"}, status_code=200 if j else 404)


@router.post("/api/import")
async def import_book(request: Request) -> JSONResponse:
    payload = await request.json()
    legacy_mod = _legacy()
    jid = legacy_mod._start("import", lambda j: legacy_mod.import_book(j, payload))
    return JSONResponse({"job": jid})


@router.post("/api/video")
async def make_film(request: Request) -> JSONResponse:
    payload = await request.json()
    legacy_mod = _legacy()
    jid = legacy_mod._start("video", lambda j: legacy_mod.make_film(j, payload))
    return JSONResponse({"job": jid})


# ------------------------------------------------------------------ TTS（旧 GET 兼容）
@router.get("/api/tts")
async def tts_compat(text: str = "", probe: str = "0", voice: str = "", rate: str = ""):
    """``/api/tts?text=`` 保持可用（阅读器逐句朗读），引擎是 backend 的 TTS Provider。"""
    from .main import get_state

    body = (text or "").strip()[:400]
    is_probe = (probe or "").strip().lower() in {"1", "true", "yes", "on"}
    if not body and not is_probe:
        return JSONResponse({"error": "empty text"}, status_code=400)
    st = get_state()
    if is_probe:
        return JSONResponse({"ok": True, "engine": st.tts.name, "cached": False, "chars": len(body)})
    lang = detect_lang(body)
    try:
        chunks = [chunk async for chunk in st.tts.stream(body, lang)]
        pcm = b"".join(chunks)
        if not pcm:
            raise RuntimeError("empty audio")
    except Exception as exc:  # noqa: BLE001 - 前端据此回落浏览器 TTS
        return JSONResponse(
            {"error": f"tts unavailable: {type(exc).__name__}", "fallback": "l1"},
            status_code=503,
            headers={"X-TTS-Chars": str(len(body))},
        )
    sample_rate = int(getattr(st.tts, "sample_rate", 22050))
    return Response(
        wav_bytes(pcm, sample_rate),
        media_type="audio/wav",
        headers={
            "X-TTS-Cache": "miss",
            "X-TTS-Chars": str(len(body)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
