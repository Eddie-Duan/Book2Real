"""Interactive reader server: import a book, read it, generate its film.

Standard library only. The analysis and film renderer run in worker threads
and report progress through an in-memory job table that the page polls.
"""
from __future__ import annotations

import base64
import io
import json
import re
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

from .util import ensure_dir, write_json

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / "web"
RUNS = ROOT / "runs"

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


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


class Handler(BaseHTTPRequestHandler):
    server_version = "Book2RealReader/0.2"

    def log_message(self, fmt, *args):
        pass

    # ---------------------------------------------------------------- utils
    def _send(self, code, body: bytes, ctype="application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, code, msg):
        self._json({"error": msg}, code)

    def _body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def _file(self, path: Path):
        if not path.is_file():
            return self._error(404, "not found")
        ctype = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".mp4": "video/mp4", ".wav": "audio/wav"}.get(path.suffix.lower(),
                                                              "application/octet-stream")
        data = path.read_bytes()
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
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(chunk)
                return
        self._send(200, data, ctype, {"Accept-Ranges": "bytes"})

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        if p == "/":
            return self._file(WEB / "reader.html")
        if p.startswith("/static/"):
            name = p.rsplit("/", 1)[-1]
            if name in ("reader.css", "reader.js"):
                return self._file(WEB / name)
            return self._error(404, "not found")
        if p == "/api/books":
            books = []
            for d in _book_dirs():
                try:
                    books.append(_book_summary(d))
                except Exception:
                    pass
            return self._json({"books": books})
        if p == "/api/book":
            bid = (q.get("book") or [""])[0]
            d = ROOT / "books" / bid / "reader" / "book.json"
            if not d.is_file():
                return self._error(404, "book not found")
            return self._json(json.loads(d.read_text(encoding="utf-8")))
        if p.startswith("/books/"):
            rel = p[len("/books/"):]
            target = (ROOT / "books" / rel).resolve()
            if str(target).startswith(str((ROOT / "books").resolve())):
                return self._file(target)
            return self._error(403, "forbidden")
        if p.startswith("/media/"):
            rel = p[len("/media/"):]
            target = (RUNS / rel).resolve()
            if str(target).startswith(str(RUNS.resolve())):
                return self._file(target)
            return self._error(403, "forbidden")
        if p == "/api/job":
            jid = (q.get("id") or [""])[0]
            j = JOBS.get(jid)
            return self._json(j or {"error": "unknown job"}, 200 if j else 404)
        if p == "/api/tts":
            text = (q.get("text") or [""])[0][:180]
            if not text.strip():
                return self._error(400, "empty text")
            try:
                from .tts import synth
                path, _ = synth(text)
                data = path.read_bytes()
                ctype = "audio/mpeg" if path.suffix == ".mp3" else "audio/wav"
                return self._send(200, data, ctype, {"Cache-Control": "public, max-age=86400"})
            except Exception as exc:                # noqa: BLE001
                return self._error(503, f"tts unavailable: {type(exc).__name__}")
        return self._error(404, "not found")

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
        except Exception as exc:                       # noqa: BLE001
            return self._error(500, f"{type(exc).__name__}: {exc}")
        return self._error(404, "not found")


def serve(books_dir="books", port: int = 8770, open_browser: bool = False) -> int:
    global ROOT
    ROOT = Path(books_dir).resolve().parent if Path(books_dir).name == "books" else ROOT
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"[reader] {url}")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
