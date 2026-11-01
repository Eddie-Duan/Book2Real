"""Tiny stdlib web server: the film player and the run dashboard.

No framework, no build step — it serves `runs/` plus a static app. Range
requests are implemented because <video> seeking depends on them.
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

WEB_DIR = Path(__file__).resolve().parent / "web"
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _runs_index(root: Path) -> list[dict]:
    out = []
    for man in sorted(root.glob("*/*/manifest.json"), reverse=True):
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "book": m.get("book_id") or man.parent.parent.name,
            "run": m.get("run_id") or man.parent.name,
            "created": m.get("created_at", ""),
            "stats": m.get("stats", {}),
            "film": {k: m.get("film", {}).get(k) for k in ("duration", "path")},
            "title": (m.get("film", {}).get("chapters") or [{}])[0].get("text", ""),
        })
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "Book2Real/0.1"
    root: Path = Path("runs")

    # ------------------------------------------------------------ helpers
    def _send(self, code: int, body: bytes, ctype: str = "application/octet-stream",
              extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj):
        self._send(200, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _file(self, path: Path):
        if not path.exists() or not path.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        rng = self.headers.get("Range")
        if rng:
            m = RANGE_RE.match(rng)
            if m:
                size = len(data)
                start = int(m.group(1) or 0)
                end = int(m.group(2)) if m.group(2) else size - 1
                end = min(end, size - 1)
                chunk = data[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return self.wfile.write(chunk)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return self.wfile.write(data)

    # ------------------------------------------------------------- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):                                  # noqa: N802
        u = urlparse(self.path)
        p = unquote(u.path)
        if p in ("/", "/index.html"):
            return self._file(WEB_DIR / "index.html")
        if p.startswith("/a/"):
            f = WEB_DIR / p[3:]
            if f.suffix in (".js", ".css", ".svg", ".png"):
                return self._file(f)
            return self._send(404, b"no", "text/plain")
        if p == "/api/runs":
            return self._json(_runs_index(self.root))
        if p.startswith("/api/manifest"):
            book = u.query.split("book=")[-1].split("&")[0] if "book=" in u.query else ""
            run = u.query.split("run=")[-1].split("&")[0] if "run=" in u.query else ""
            man = self.root / book / run / "manifest.json"
            if not man.exists():
                return self._send(404, b"no run", "text/plain")
            return self._file(man)
        if p.startswith("/media/"):
            rel = p[len("/media/"):]
            target = (self.root / rel).resolve()
            try:
                target.relative_to(self.root.resolve())
            except ValueError:
                return self._send(403, b"no", "text/plain")
            return self._file(target)
        return self._send(404, b"not found", "text/plain")

    def log_message(self, fmt, *args):                 # quieter console
        if "/media/" in (args[0] if args else ""):
            return


def serve(runs: str, port: int = 8765, open_browser: bool = False) -> int:
    root = Path(runs).resolve()
    if not root.exists():
        print(f"runs directory not found: {root}")
        return 2
    Handler.root = root
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"[b2r] serving {root}")
    print(f"[b2r] open {url}")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[b2r] bye")
    return 0
