"""Interactive reader server: import a book, read it, generate its film.

Standard library only. The analysis and film renderer run in worker threads
and report progress through an in-memory job table that the page polls.

The routes and their headers follow ``docs/INTEGRATION_CONTRACT.md`` §1:
``/api/tts`` (probe + cache headers + LRU cap), ``/api/ask`` (lazy ``qa`` module
with a hard fallback), ``/api/books/<id>/files``, a directory-wide ``/static/``
and CSP/nosniff on every response.  The server binds ``127.0.0.1`` by default.
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
    global ROOT
    ROOT = Path(books_dir).resolve().parent if Path(books_dir).name == "books" else ROOT
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
