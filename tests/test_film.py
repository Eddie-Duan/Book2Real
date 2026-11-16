"""Film player gate tests: timeline shape, source-size backfill, Range video.

No pytest, no network.  Builds a throwaway ``runs/`` + ``books/`` tree, points
``reader_app`` at it and serves on a free loopback port with the real ``Handler``
(same routes, same security headers), then probes ``/film``, ``/api/film/<id>``
and ``/api/film/<id>/video``.

    python tests/test_film.py

Last line prints ``N tests passed``.
"""
from __future__ import annotations

import http.client
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image

from book2real import reader_app

BID = "filmtest"
RUN = "20260101-000000"
VIDEO = bytes(range(256)) * 4                       # 1024 deterministic bytes
TIMELINE = {
    "book_id": BID,
    "video": "film.mp4",
    "width": 1280,
    "height": 720,
    "fps": 24,
    "duration": 21.0,
    "language": "zh",
    "title": "测试影片",
    "segments": [
        {"kind": "cover", "id": "p01", "source_page": 1, "t0": 0.0, "t1": 2.0,
         "art_rect": [640, 120, 600, 600], "art": "art/p01.jpg",
         "art_w": 800, "art_h": 800, "text": "测试影片", "scene": "",
         "spots": [], "characters": [], "ocr_lines": [], "text_boxes": []},
        # source size comes from reader/book.json
        {"kind": "page", "id": "p02", "source_page": 2, "t0": 2.0, "t1": 12.0,
         "art_rect": [640, 135, 600, 450], "art": "art/p02.jpg",
         "art_w": 800, "art_h": 450,
         "text": "下雨了，我们一起搭积木。",
         "scene": "下雨天，两个孩子在客厅里搭积木。",
         "spots": [{"name": "积木城堡", "box": [600, 400, 300, 200],
                    "desc": "搭了九块积木，还没有倒"}],
         "characters": [],
         "ocr_lines": [
             {"text": "我们一起搭积木，", "score": 0.91, "box": [231, 164, 757, 242]},
             {"text": "生日快乐", "score": 0.88, "box": [240, 300, 600, 360]}],
         "text_boxes": [[232, 271, 862, 320]]},
        # book.json has no source_w/h here: the server must read pages/p03.jpg
        {"kind": "page", "id": "p03", "source_page": 3, "t0": 12.0, "t1": 18.0,
         "art_rect": [640, 135, 600, 450], "art": "art/p03.jpg",
         "art_w": 800, "art_h": 450, "text": "三块、四块、五块。", "scene": "",
         "spots": [], "characters": [], "ocr_lines": [], "text_boxes": []},
        # nothing on disk: source size falls back to art_w/art_h
        {"kind": "page", "id": "p04", "source_page": 4, "t0": 18.0, "t1": 19.0,
         "art_rect": [640, 135, 600, 450], "art": "art/p04.jpg",
         "art_w": 800, "art_h": 450, "text": "积木城堡倒了。", "scene": "",
         "spots": [], "characters": [], "ocr_lines": [], "text_boxes": []},
        {"kind": "end", "id": "end", "t0": 19.0, "t1": 21.0,
         "art_rect": None, "art": "", "text": "", "scene": "",
         "spots": [], "characters": [], "ocr_lines": [], "text_boxes": []},
    ],
}

STATE: dict = {"httpd": None, "port": 0, "tmp": None, "root": None, "runs": None}


# ------------------------------------------------------------------ fixture
def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def _fixtures(root: Path) -> None:
    run = root / "runs" / BID / "reader_film" / RUN
    _write(run / "timeline.json", json.dumps(TIMELINE, ensure_ascii=False))
    _write(run / "film.mp4", VIDEO)

    book = root / "books" / BID / "reader"
    _write(book / "book.json", json.dumps({
        "book_id": BID, "title": "测试绘本", "fps": 24,
        "pages": [
            {"id": "p01", "kind": "cover", "source_page": 1, "art": "art/p01.jpg",
             "art_w": 800, "art_h": 800, "source_w": 1600, "source_h": 1600},
            {"id": "p02", "kind": "page", "source_page": 2, "art": "art/p02.jpg",
             "art_w": 800, "art_h": 450, "source_w": 2000, "source_h": 900},
            {"id": "p03", "kind": "page", "source_page": 3, "art": "art/p03.jpg",
             "art_w": 800, "art_h": 450},
            {"id": "p04", "kind": "page", "source_page": 4, "art": "art/p04.jpg",
             "art_w": 800, "art_h": 450},
        ]}, ensure_ascii=False))
    # the real source page for p03: 2000x900, read with PIL by the server
    jpg = root / "books" / BID / "pages" / "p03.jpg"
    jpg.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2000, 900), (250, 246, 238)).save(jpg)

    # a book that exists but has no film: must be a 404, not a 500
    _write(root / "books" / "nofilm" / "reader" / "book.json", json.dumps({
        "book_id": "nofilm", "title": "还没有成片", "fps": 24,
        "pages": [{"id": "p01", "kind": "cover", "source_page": 1,
                   "art": "art/p01.jpg", "art_w": 800, "art_h": 800,
                   "source_w": 800, "source_h": 800}]},
        ensure_ascii=False))


# ------------------------------------------------------------------ harness
def _start() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="b2r_film_"))
    _fixtures(tmp)
    STATE.update(tmp=tmp, root=reader_app.ROOT, runs=reader_app.RUNS)
    reader_app.ROOT = tmp
    reader_app.RUNS = tmp / "runs"
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), reader_app.Handler)
    STATE.update(httpd=httpd, port=httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    deadline = 0
    while deadline < 100:
        try:
            with socket.create_connection(("127.0.0.1", STATE["port"]), timeout=0.5):
                return
        except OSError:
            deadline += 1


def _stop() -> None:
    httpd = STATE.get("httpd")
    if httpd is not None:
        httpd.shutdown()
        httpd.server_close()
    if STATE.get("root") is not None:
        reader_app.ROOT = STATE["root"]
    if STATE.get("runs") is not None:
        reader_app.RUNS = STATE["runs"]
    tmp = STATE.get("tmp")
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)


def _http(path: str, method: str = "GET", headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", STATE["port"], timeout=20)
    hdrs = {"Connection": "close", "Accept": "*/*"}
    hdrs.update(headers or {})
    conn.request(method, path, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    got = {k.lower(): v for k, v in resp.getheaders()}
    status = resp.status
    conn.close()
    return status, got, data


def _json_body(path: str):
    status, hdrs, data = _http(path)
    assert hdrs.get("content-type", "").startswith("application/json"), hdrs
    return status, hdrs, json.loads(data.decode("utf-8"))


def _raw(path: str):
    """Send the request target verbatim: no client-side path normalisation."""
    s = socket.create_connection(("127.0.0.1", STATE["port"]), timeout=20)
    try:
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
                  .encode("latin-1"))
        chunks = []
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        s.close()
    raw = b"".join(chunks)
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n", 1)[0].split()[1])
    return status, body


# ------------------------------------------------------------------ tests
def test_film_html_route_has_csp():
    status, hdrs, body = _http(f"/film?book={BID}")
    assert status == 200, status
    assert hdrs.get("content-type", "").startswith("text/html"), hdrs
    assert hdrs.get("x-content-type-options") == "nosniff", hdrs
    assert hdrs.get("referrer-policy") == "no-referrer", hdrs
    csp = hdrs.get("content-security-policy", "")
    assert "default-src 'self'" in csp and "script-src 'self'" in csp, csp
    assert b"film.js" in body and b"film.css" in body, body[:200]
    assert b'lang="zh-CN"' in body


def test_timeline_backfills_source_size():
    status, _h, payload = _json_body(f"/api/film/{BID}")
    assert status == 200, status
    segs = payload["segments"]
    assert len(segs) == len(TIMELINE["segments"]) == 5, len(segs)
    assert [s["kind"] for s in segs] == ["cover", "page", "page", "page", "end"]
    assert payload["video_url"] == f"/api/film/{BID}/video", payload.get("video_url")
    assert payload["mapping"] == "fallback", payload["mapping"]   # p04 has no source size

    by_id = {s["id"]: s for s in segs}
    # cover/page sizes come from reader/book.json
    assert (by_id["p01"]["source_w"], by_id["p01"]["source_h"]) == (1600, 1600)
    assert by_id["p01"]["mapping"] == "book", by_id["p01"]["mapping"]
    assert (by_id["p02"]["source_w"], by_id["p02"]["source_h"]) == (2000, 900)
    assert by_id["p02"]["mapping"] == "book"
    # p03 has no source_w/h in book.json: the server reads pages/p03.jpg with PIL
    assert (by_id["p03"]["source_w"], by_id["p03"]["source_h"]) == (2000, 900), by_id["p03"]
    assert by_id["p03"]["mapping"] == "pages", by_id["p03"]["mapping"]
    # p04 has nothing: fall back to art_w/art_h and say so
    assert by_id["p04"]["source_w"] == 800 and by_id["p04"]["mapping"] == "fallback", by_id["p04"]
    # the closing card has no art, so no source page
    assert by_id["end"]["source_w"] == 0 and by_id["end"]["mapping"] == "none", by_id["end"]

    # the boxes the page needs are still in the payload
    page = by_id["p02"]
    assert page["art_rect"] == [640, 135, 600, 450]
    assert page["ocr_lines"][0]["box"] == [231, 164, 757, 242]
    assert page["spots"][0]["box"] == [600, 400, 300, 200]


def test_timeline_missing_book_is_404_json():
    for bid in ("__nope__", "nofilm"):
        status, hdrs, payload = _json_body(f"/api/film/{bid}")
        assert status == 404, (bid, status)
        assert "error" in payload, payload
        assert hdrs.get("x-content-type-options") == "nosniff", hdrs


def test_video_full_and_range():
    status, hdrs, data = _http(f"/api/film/{BID}/video")
    assert status == 200, status
    assert hdrs.get("content-type") == "video/mp4", hdrs
    assert hdrs.get("accept-ranges") == "bytes", hdrs
    assert hdrs.get("content-length") == str(len(VIDEO)), hdrs
    assert data == VIDEO

    status, hdrs, data = _http(f"/api/film/{BID}/video", headers={"Range": "bytes=0-99"})
    assert status == 206, status
    assert hdrs.get("content-range") == f"bytes 0-99/{len(VIDEO)}", hdrs
    assert hdrs.get("content-type") == "video/mp4", hdrs
    assert hdrs.get("accept-ranges") == "bytes", hdrs
    assert hdrs.get("content-length") == "100", hdrs
    assert data == VIDEO[:100], len(data)

    # scrubbing forward uses open-ended and suffix ranges too
    status, hdrs, data = _http(f"/api/film/{BID}/video", headers={"Range": "bytes=1000-"})
    assert status == 206 and hdrs.get("content-range") == f"bytes 1000-1023/{len(VIDEO)}", hdrs
    assert data == VIDEO[1000:]

    status, hdrs, data = _http(f"/api/film/{BID}/video", headers={"Range": "bytes=-24"})
    assert status == 206 and hdrs.get("content-range") == f"bytes 1000-1023/{len(VIDEO)}", hdrs
    assert data == VIDEO[-24:]


def test_video_unsatisfiable_range_is_416():
    status, hdrs, _data = _http(f"/api/film/{BID}/video", headers={"Range": "bytes=5000-6000"})
    assert status == 416, status
    assert hdrs.get("content-range") == f"bytes */{len(VIDEO)}", hdrs


def test_film_paths_traversal_is_rejected():
    for target in (f"/api/film/../{BID}",
                   f"/api/film/..%2f..%2f{BID}",
                   f"/api/film/{BID}/../..%2f..%2fWindows/video",
                   "/api/film/%2e%2e/video",
                   "/api/film/../../books/nofilm/reader/book.json"):
        status, body = _raw(target)
        assert status in (403, 404), (target, status)
        assert b"book_id" not in body, target          # never leaks a real file


def test_video_route_unknown_film_is_404_json():
    status, _h, payload = _json_body("/api/film/nofilm/video")
    assert status == 404, status
    assert "error" in payload, payload


def test_static_film_assets_and_node_check():
    web = ROOT / "book2real" / "web"
    for name in ("film.js", "film.css", "film.html"):
        status, hdrs, data = _http(f"/static/{name}")
        assert status == 200, (name, status)
        assert data == (web / name).read_bytes(), name
        assert hdrs.get("x-content-type-options") == "nosniff", (name, hdrs)
    js = _http("/static/film.js")[2].decode("utf-8")
    for marker in ("api(\"/api/film/", "art_rect", "source_w", "webkitSpeechRecognition"):
        assert marker in js, marker
    node = shutil.which("node")
    if not node:
        return "skip"                                  # no node on this box
    done = subprocess.run([node, "--check", str(web / "film.js")],
                          capture_output=True, text=True)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)


def test_existing_routes_still_answer():
    """The film routes must not have taken /api/books or the reader page away."""
    status, _h, payload = _json_body("/api/books")
    assert status == 200, status
    assert any(b["id"] == BID for b in payload["books"]), payload
    status, hdrs, body = _http("/")
    assert status == 200 and b"<html" in body.lower(), status
    assert "default-src 'self'" in hdrs.get("content-security-policy", ""), hdrs
    status, _h, payload = _json_body("/api/nope")
    assert status == 404 and "error" in payload


if __name__ == "__main__":
    ok = 0
    try:
        _start()
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                result = fn()
                if result == "skip":
                    print(f"skip {name}")
                else:
                    ok += 1
                    print(f"ok  {name}")
    finally:
        _stop()
    print(f"{ok} tests passed")
