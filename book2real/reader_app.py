"""Interactive reader server: import a book, read it, generate its film.

Standard library only. The analysis and film renderer run in worker threads
and report progress through an in-memory job table that the page polls.

The routes and their headers follow ``docs/INTEGRATION_CONTRACT.md`` §1:
``/api/tts`` (probe + cache headers + LRU cap), ``/api/ask`` (lazy ``qa`` module
with a hard fallback), ``/api/books/<id>/files``, ``/film`` + ``/api/film/<id>``
(timeline with ``source_w/source_h`` backfilled) + ``/api/film/<id>/video``
(Range-capable mp4), a directory-wide ``/static/`` and CSP/nosniff on every
response.  The server binds ``127.0.0.1`` by default.
"""
from __future__ import annotations

import base64
import io
import json
import re
import socket
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image

from .util import ensure_dir, write_json

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / "web"
RUNS = ROOT / "runs"

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

#: Contract §1.5.  ``style-src 'unsafe-inline'`` stays: reader.js builds
#: ``style="..."`` attributes; ``reader.html`` only links one external script.
CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
       "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'")

HTML_HEADERS = {
    "Content-Security-Policy": CSP,
    "Referrer-Policy": "no-referrer",
}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
}

ASK_FALLBACK_ANSWER = "问答服务还没就绪，先看看这一页的图吧。"
ASK_FALLBACKS = (None, "no_llm_key", "llm_error", "no_evidence", "qa_unavailable")


# ------------------------------------------------------------------ paths
def _within(base: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``base``; None when it escapes or cannot resolve.

    Replaces the old ``str(...).startswith(...)`` guard, which let a sibling
    directory (``booksX/``) through because the prefix matched.
    """
    try:
        base_r = base.resolve()
        target = (base / unquote(rel)).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return target if target.is_relative_to(base_r) else None


# ------------------------------------------------------------------ jobs
def _job_update(jid: str, **kw) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(jid, {})["updated"] = time.time()
        JOBS[jid].update(kw)


def _start(kind: str, target) -> str:
    jid = uuid.uuid4().hex[:10]
    with JOBS_LOCK:
        JOBS[jid] = {"id": jid, "kind": kind, "status": "running",
                     "stage": "准备中", "progress": 0, "started": time.time()}

    def run():
        try:
            target(jid)
            _job_update(jid, status="done", progress=1.0, stage="完成")
        except Exception as exc:                       # noqa: BLE001 - surface to UI
            _job_update(jid, status="error", stage="失败", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=run, daemon=True).start()
    return jid


def _progress(jid: str):
    def cb(done: int, total: int, stage: str):
        _job_update(jid, progress=round(done / max(1, total), 3), stage=stage)
    return cb


def _slug(title: str) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", title).strip("_")
    return (s[:24] or "book") + "_" + time.strftime("%H%M%S")


def _book_dirs() -> list[Path]:
    return [p for p in sorted((ROOT / "books").glob("*")) if (p / "reader" / "book.json").exists()]


def _book_summary(d: Path) -> dict:
    data = json.loads((d / "reader" / "book.json").read_text(encoding="utf-8"))
    cover = next((p for p in data["pages"] if p.get("kind") == "cover"), data["pages"][0])
    return {"id": data["book_id"], "title": data["title"], "subtitle": data.get("subtitle", ""),
            "pages": len(data["pages"]), "cover": cover["art"], "built_at": data.get("built_at", "")}


def _book_files(book_id: str) -> tuple[int, dict]:
    """List ``books/<id>/reader/**``. Returns (status, payload)."""
    bid = (book_id or "").strip().strip("/")
    if not bid or "/" in bid or "\\" in bid:
        return 404, {"error": "book not found"}
    book_dir = _within(ROOT / "books", bid)
    if book_dir is None:
        return 403, {"error": "forbidden"}
    reader = book_dir / "reader"
    if not reader.is_dir():
        return 404, {"error": "book not found"}
    files: list[dict] = []
    total = 0
    try:
        for f in sorted(reader.rglob("*")):
            if not f.is_file():
                continue
            if _within(reader, str(f.relative_to(reader))) is None:
                continue
            try:
                size = f.stat().st_size
            except OSError:
                continue
            files.append({"path": f.relative_to(book_dir).as_posix(), "bytes": size})
            total += size
    except OSError:
        return 500, {"error": "cannot list files"}
    return 200, {"book": bid, "bytes": total, "files": files}


# ------------------------------------------------------------------ film
#: ``/api/film/<id>`` is read-only over ``runs/``: the newest run directory that
#: holds both ``timeline.json`` and ``film.mp4`` wins.  The timeline is handed to
#: the page as-is except for ``source_w``/``source_h``, which older runs never
#: wrote — the OCR boxes are in *source page* pixels, so without them the page
#: cannot map a line back onto the frame.
def _clean_book_id(book_id: str) -> str:
    """A bare book id, or ``""``.  Rejects anything that could walk a path."""
    bid = (book_id or "").strip().strip("/")
    if not bid or bid in (".", "..") or "/" in bid or "\\" in bid:
        return ""
    return bid


def _film_dir(book_id: str) -> Path | None:
    """Newest ``runs/<id>/reader_film/*/`` with both timeline and video."""
    bid = _clean_book_id(book_id)
    if not bid:
        return None
    base = _within(RUNS, bid)
    if base is None:
        return None
    films = base / "reader_film"
    if not films.is_dir():
        return None
    try:
        cands = [p for p in films.iterdir()
                 if p.is_dir()
                 and (p / "timeline.json").is_file()
                 and (p / "film.mp4").is_file()]
    except OSError:
        return None
    # run names are ``YYYYmmdd-HHMMSS``, so the lexical max is the newest run
    return max(cands, key=lambda p: p.name) if cands else None


def _int_or_none(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _book_pages(book_id: str) -> list[dict]:
    """``books/<id>/reader/book.json`` pages, or ``[]`` when unreadable."""
    book_dir = _within(ROOT / "books", book_id)
    if book_dir is None:
        return []
    try:
        data = json.loads((book_dir / "reader" / "book.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    pages = data.get("pages") if isinstance(data, dict) else None
    return [p for p in pages or [] if isinstance(p, dict)]


def _page_size_from_jpeg(book_id: str, page: dict) -> tuple[int, int]:
    """Read the source page size straight off ``books/<id>/pages/pNN.jpg``."""
    book_dir = _within(ROOT / "books", book_id)
    if book_dir is None:
        return 0, 0
    names: list[str] = []
    num = _int_or_none(page.get("source_page"))
    if num:
        names += [f"p{num:02d}.jpg", f"p{num}.jpg"]
    pid = str(page.get("id") or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", pid):
        names.append(f"{pid}.jpg")
    for name in names:
        target = _within(book_dir / "pages", name)
        if target is None or not target.is_file():
            continue
        try:
            with Image.open(target) as im:      # header only, no pixels decoded
                return int(im.width), int(im.height)
        except (OSError, ValueError):
            continue
    return 0, 0


def _segment_source_size(book_id: str, seg: dict, pages: list[dict],
                         by_id: dict) -> tuple[int, int, str]:
    """(source_w, source_h, how) for one segment; ``how`` is book|pages|fallback."""
    page = by_id.get(str(seg.get("id") or ""))
    if page is None:
        num = _int_or_none(seg.get("source_page"))
        if num and 1 <= num <= len(pages):
            page = pages[num - 1]
    if page is not None:
        w, h = _int_or_none(page.get("source_w")), _int_or_none(page.get("source_h"))
        if w and h:
            return w, h, "book"
    if page is not None:
        w, h = _page_size_from_jpeg(book_id, page)
        if w and h:
            return w, h, "pages"
    w, h = _int_or_none(seg.get("art_w")), _int_or_none(seg.get("art_h"))
    if w and h:
        return w, h, "fallback"
    return 0, 0, "fallback"


def film_timeline(book_id: str) -> tuple[int, dict]:
    """(status, payload) for ``/api/film/<id>``.  Missing film is a 404, never 500."""
    bid = _clean_book_id(book_id)
    if not bid:
        return 404, {"error": "book not found"}
    film_dir = _film_dir(bid)
    if film_dir is None:
        return 404, {"error": "这本书还没有成片", "book": bid}
    try:
        data = json.loads((film_dir / "timeline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 500, {"error": "timeline 读不出来", "book": bid}
    segs = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segs, list):
        return 500, {"error": "timeline 格式不对", "book": bid}
    pages = _book_pages(bid)
    by_id = {str(p.get("id")): p for p in pages if p.get("id")}
    kinds: set[str] = set()
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        rect = seg.get("art_rect")
        if not (isinstance(rect, list) and len(rect) == 4):
            # the closing card has no page art, so it has no source page either
            seg["source_w"], seg["source_h"], seg["mapping"] = 0, 0, "none"
            continue
        w, h, how = _segment_source_size(bid, seg, pages, by_id)
        seg["source_w"], seg["source_h"] = w, h
        seg["mapping"] = how
        kinds.add(how)
    # the page needs to know whether the boxes are trustworthy; say so plainly
    data["mapping"] = "fallback" if "fallback" in kinds else (
        "book" if "book" in kinds else ("pages" if "pages" in kinds else "fallback"))
    data.setdefault("book_id", bid)
    data["run"] = film_dir.name
    data["video_url"] = f"/api/film/{bid}/video"
    return 200, data


def film_video(book_id: str) -> Path | None:
    """The ``film.mp4`` of the newest run, resolved inside ``runs/`` only."""
    film_dir = _film_dir(book_id)
    if film_dir is None:
        return None
    target = _within(film_dir, "film.mp4")
    if target is None or not target.is_file():
        return None
    try:                                   # belt and braces: still under runs/
        if not target.resolve().is_relative_to(RUNS.resolve()):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return target


# ------------------------------------------------------------------ ask
def ask(payload: dict) -> dict:
    """Call ``book2real.qa.answer`` lazily; never raise, never 500 (contract §1.2).

    While the qa module does not exist yet (it is written in parallel), every
    failure degrades to a readable answer with ``fallback="qa_unavailable"``.
    """
    t0 = time.perf_counter()
    req = payload if isinstance(payload, dict) else {}
    page = str(req.get("page") or "")
    try:
        from . import qa                     # lazy: ImportError is expected early on
        res = qa.answer(dict(req))
        if not isinstance(res, dict):
            raise TypeError(f"qa.answer returned {type(res).__name__}")
    except Exception:                        # noqa: BLE001 - degrade, never 500
        res = {"answer": ASK_FALLBACK_ANSWER, "citations": [], "source": "rule",
               "fallback": "qa_unavailable", "page": page,
               "socratic": {"level": 0, "directive": "none", "line": ""}}
    res.setdefault("page", page)
    res.setdefault("citations", [])
    res.setdefault("source", "rule")
    res.setdefault("fallback", None)
    res.setdefault("socratic", {"level": 0, "directive": "none", "line": ""})
    res.setdefault("latency_ms", round((time.perf_counter() - t0) * 1000, 1))
    if not isinstance(res.get("answer"), str):
        res["answer"] = str(res.get("answer") or "")
    return res


class Handler(BaseHTTPRequestHandler):
    server_version = "Book2RealReader/0.3"

    def log_message(self, fmt, *args):
        pass

    # ---------------------------------------------------------------- utils
    def _send(self, code, body: bytes, ctype="application/octet-stream", extra=None):
        extra = dict(extra or {})
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", extra.pop("Cache-Control", "no-store"))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", extra)

    def _error(self, code, msg):
        self._json({"error": msg}, code)

    def _body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def _file(self, path: Path, extra=None):
        if not path.is_file():
            return self._error(404, "not found")
        ctype = MIME.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        hdrs = {"Accept-Ranges": "bytes"}
        if path.suffix.lower() in (".html", ".htm"):
            hdrs.update(HTML_HEADERS)
        hdrs.update(extra or {})
        rng = self.headers.get("Range")
        if rng and path.suffix.lower() == ".mp4":
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                start = int(m.group(1) or 0)
                end = int(m.group(2)) if m.group(2) else len(data) - 1
                end = min(end, len(data) - 1)
                chunk = data[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(chunk)
                return
        self._send(200, data, ctype, hdrs)

    # ---------------------------------------------------------------- video
    def _range(self, size: int):
        """Parse ``Range`` for one file: None (whole file), tuple or ``"bad"``."""
        raw = (self.headers.get("Range") or "").strip()
        if not raw:
            return None
        m = re.fullmatch(r"bytes\s*=\s*(\d*)\s*-\s*(\d*)", raw)
        if not m or not (m.group(1) or m.group(2)):
            return "bad"
        first, last = m.group(1), m.group(2)
        if not first:                        # ``bytes=-N``: the last N bytes
            n = int(last)
            if n <= 0:
                return "bad"
            return max(0, size - n), size - 1
        start = int(first)
        end = int(last) if last else size - 1
        if start >= size or start > end:
            return "bad"
        return start, min(end, size - 1)

    def _video(self, path: Path):
        """Stream an mp4 with Range support, so the page can scrub the timeline."""
        if not path.is_file():
            return self._error(404, "film not found")
        try:
            size = path.stat().st_size
        except OSError:
            return self._error(404, "film not found")
        if size <= 0:
            return self._error(404, "film not found")
        rng = self._range(size)
        if rng == "bad":
            return self._json({"error": "range not satisfiable"}, 416,
                              {"Content-Range": f"bytes */{size}",
                               "Accept-Ranges": "bytes"})
        start, end = rng if rng else (0, size - 1)
        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:                                  # a 16MB film must not be read in one go
            with path.open("rb") as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    chunk = fh.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                              # the browser aborted the seek

    # ---------------------------------------------------------------- tts
    def _tts(self, q):
        """Contract §1.1: keep the old audio response, add probe + cache headers."""
        from . import tts
        text = (q.get("text") or [""])[0].strip()[:400]
        probe = (q.get("probe") or ["0"])[0].strip().lower() in ("1", "true", "yes", "on")
        if not text and not probe:
            return self._error(400, "empty text")
        voice = (q.get("voice") or [tts.VOICE])[0] or tts.VOICE
        rate = (q.get("rate") or [tts.RATE])[0] or tts.RATE
        chars = str(len(text))
        try:
            if probe:
                # a bare /api/tts?probe=1 is a legal health probe (chars=0, miss)
                info = tts.probe(text, voice=voice, rate=rate)
                return self._json(
                    {"ok": True, **info}, 200,
                    {"X-TTS-Cache": "hit" if info["cached"] else "miss", "X-TTS-Chars": chars})
            hit = tts.cached_path(text, voice=voice, rate=rate) is not None
            path, _dur = tts.synth(text, voice=voice, rate=rate)
            data = path.read_bytes()
            ctype = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
            return self._send(200, data, ctype, {
                "X-TTS-Cache": "hit" if hit else "miss",
                "X-TTS-Chars": chars,
                "Cache-Control": "public, max-age=86400"})
        except Exception as exc:                # noqa: BLE001 - the page falls back to L1
            return self._json({"error": f"tts unavailable: {type(exc).__name__}",
                               "fallback": "l1"}, 503, {"X-TTS-Chars": chars})

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        if p in ("/", "/index.html"):
            return self._file(WEB / "reader.html")
        if p.startswith("/static/"):
            name = p[len("/static/"):]
            target = _within(WEB, name)
            if target is None:
                return self._error(403, "forbidden")
            extra = {"Service-Worker-Allowed": "/"} if target.name == "sw.js" else None
            return self._file(target, extra)
        if p == "/api/books":
            books = []
            for d in _book_dirs():
                try:
                    books.append(_book_summary(d))
                except Exception:
                    pass
            return self._json({"books": books})
        if p.startswith("/api/books/") and p.endswith("/files"):
            bid = unquote(p[len("/api/books/"):-len("/files")])
            code, payload = _book_files(bid)
            return self._json(payload, code)
        if p == "/api/book":
            bid = (q.get("book") or [""])[0]
            target = _within(ROOT / "books", bid)
            d = (target / "reader" / "book.json") if target else None
            if not d or not d.is_file():
                return self._error(404, "book not found")
            return self._json(json.loads(d.read_text(encoding="utf-8")))
        if p.startswith("/books/"):
            target = _within(ROOT / "books", p[len("/books/"):])
            if target is None:
                return self._error(403, "forbidden")
            return self._file(target)
        if p.startswith("/media/"):
            target = _within(RUNS, p[len("/media/"):])
            if target is None:
                return self._error(403, "forbidden")
            return self._file(target)
        if p == "/film":
            return self._file(WEB / "film.html")
        if p.startswith("/api/film/"):
            rest = p[len("/api/film/"):]
            if rest.endswith("/video"):
                target = film_video(unquote(rest[:-len("/video")]))
                if target is None:
                    return self._error(404, "film not found")
                return self._video(target)
            code, payload = film_timeline(unquote(rest))
            return self._json(payload, code)
        if p == "/api/job":
            jid = (q.get("id") or [""])[0]
            j = JOBS.get(jid)
            return self._json(j or {"error": "unknown job"}, 200 if j else 404)
        if p == "/api/tts":
            return self._tts(q)
        if p.startswith("/api/"):
            return self._error(404, "unknown api")
        return self._error(404, "not found")

    do_HEAD = do_GET

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/import":
                payload = self._body_json()
                jid = _start("import", lambda j: import_book(j, payload))
                return self._json({"job": jid})
            if u.path == "/api/video":
                payload = self._body_json()
                jid = _start("video", lambda j: make_film(j, payload))
                return self._json({"job": jid})
            if u.path == "/api/ask":
                try:
                    payload = self._body_json()
                except Exception:              # noqa: BLE001 - empty/garbled body still 200
                    payload = {}
                return self._json(ask(payload))
        except Exception as exc:                       # noqa: BLE001
            return self._error(500, f"{type(exc).__name__}: {exc}")
        return self._error(404, "not found")


# ------------------------------------------------------------------ import / film
def import_book(jid: str, payload: dict) -> None:
    title = (payload.get("title") or "未命名绘本").strip()
    book_id = payload.get("book_id") or _slug(title)
    files = payload.get("files") or []
    if not files:
        raise ValueError("没有收到页面图片")
    files.sort(key=lambda f: f.get("lastModified", 0))
    book_dir = ensure_dir(ROOT / "books" / book_id)
    pages_dir = ensure_dir(book_dir / "pages")
    pages = []
    for i, f in enumerate(files, start=1):
        raw = f.get("data", "")
        if "," in raw:
            raw = raw.split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")
        name = f"p{i:02d}.jpg"
        img.save(pages_dir / name, quality=94)
        mode = "fill" if img.width / img.height >= 1.2 else "card"
        pages.append({"file": name, "move": "kenburns", "mode": mode, "duration": 5.0})
    write_json(book_dir / "book.json", {
        "book_id": book_id, "title": title, "subtitle": "AI 互动绘本",
        "fps": 24, "layers": False, "pages": pages})
    _job_update(jid, stage="识别文字与清理插图")

    from .storybook import build
    build(book_dir, force=True, on_progress=_progress(jid))
    _job_update(jid, result={"book": book_id})


def make_film(jid: str, payload: dict) -> None:
    from .storyfilm import render
    book_id = payload.get("book")
    book_dir = ROOT / "books" / book_id
    if not (book_dir / "reader" / "book.json").exists():
        raise FileNotFoundError(f"book not prepared: {book_id}")
    _job_update(jid, stage="准备画面与旁白")
    out = render(book_dir, on_progress=_progress(jid))
    _job_update(jid, result=out)


# ------------------------------------------------------------------ server
def _lan_ip() -> str:
    """Best-effort LAN address without sending a packet (UDP connect is local)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))            # TEST-NET-1, never routed
        ip = s.getsockname()[0]
        return ip if ip and not ip.startswith("0.") else "127.0.0.1"
    except OSError:
        return "127.0.0.1"
    finally:
        try:
            s.close()
        except OSError:
            pass


LOOPBACK = ("127.0.0.1", "localhost", "::1")


def serve(books_dir="books", port: int = 8770, open_browser: bool = False,
          host: str = "127.0.0.1") -> int:
    global ROOT, RUNS
    ROOT = Path(books_dir).resolve().parent if Path(books_dir).name == "books" else ROOT
    RUNS = ROOT / "runs"                      # /api/film reads runs/ next to books/
    httpd = ThreadingHTTPServer((host, port), Handler)
    bound = httpd.server_address[1]
    url = f"http://127.0.0.1:{bound}/"
    print(f"[reader] 本机   {url}", flush=True)
    if host in LOOPBACK:
        print("[reader] 局域网 当前只监听本机；加 --host 0.0.0.0 让同 Wi-Fi 的平板访问",
              flush=True)
    else:
        print(f"[reader] 局域网 http://{_lan_ip()}:{bound}/  (host={host})", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
