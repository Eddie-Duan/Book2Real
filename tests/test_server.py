"""Service gate tests: spawn the real reader server and hit it over HTTP.

No pytest, no network.  Starts ``python -m book2real reader --port <free port>``
in a subprocess, probes every route from docs/INTEGRATION_CONTRACT.md §1, and
always kills the subprocess in ``finally`` (a leaked server would poison the
next test run).

    python tests/test_server.py

Last line prints ``N tests passed``.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book2real import tts as T

STATE: dict = {"proc": None, "port": 0, "log": None, "log_path": None,
               "fake": None, "skipped": 0}
QA_FILE = ROOT / "book2real" / "qa.py"

ALLOWED_FALLBACKS = (None, "no_llm_key", "llm_error", "no_evidence", "qa_unavailable")


# ------------------------------------------------------------------ harness
def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _start() -> None:
    port = _free_port()
    tmp = Path(tempfile.mkdtemp(prefix="b2r_server_"))
    log_path = tmp / "server.log"
    out = open(log_path, "wb")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "book2real", "reader", "--port", str(port),
         "--books", str(ROOT / "tests" / "fixtures" / "books")],
        cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out.close()
    STATE.update(proc=proc, port=port, log_path=log_path)
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited rc={proc.returncode}\n{_log_tail()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"server did not listen on port {port}\n{_log_tail()}")


def _log_tail(n: int = 3000) -> str:
    p = STATE.get("log_path")
    if not p or not p.exists():
        return "<no log>"
    return p.read_text(encoding="utf-8", errors="replace")[-n:]


def _stop() -> None:
    proc = STATE.get("proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                pass
    fake = STATE.get("fake")
    if fake is not None:
        try:
            Path(fake).unlink(missing_ok=True)
        except OSError:
            pass


def _http(path: str, method: str = "GET", body: bytes | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", STATE["port"], timeout=20)
    headers = {"Connection": "close", "Accept": "*/*"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    hdrs = {k.lower(): v for k, v in resp.getheaders()}
    status = resp.status
    conn.close()
    return status, hdrs, data


def _json_body(path: str, method: str = "GET", body: bytes | None = None):
    status, hdrs, data = _http(path, method, body)
    assert hdrs.get("content-type", "").startswith("application/json"), hdrs
    return status, hdrs, json.loads(data.decode("utf-8"))


def _raw(path: str) -> int:
    """Send the request target verbatim (no client-side normalisation)."""
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
    head = b"".join(chunks).split(b"\r\n", 1)[0].decode("latin-1")
    return int(head.split()[1])


def _first_book() -> str:
    status, _h, payload = _json_body("/api/books")
    assert status == 200
    books = payload.get("books") or []
    assert books, payload
    return books[0]["id"]


# ------------------------------------------------------------------ tests
def test_index_has_csp_and_nosniff():
    status, hdrs, body = _http("/")
    assert status == 200, status
    csp = hdrs.get("content-security-policy", "")
    assert "default-src 'self'" in csp, csp
    assert "style-src 'self' 'unsafe-inline'" in csp, csp
    assert "script-src 'self'" in csp, csp
    assert "img-src 'self' data: blob:" in csp, csp
    assert "media-src 'self' blob:" in csp, csp
    assert "connect-src 'self'" in csp, csp
    assert hdrs.get("x-content-type-options") == "nosniff", hdrs
    assert hdrs.get("referrer-policy") == "no-referrer", hdrs
    assert b"<html" in body.lower()


def test_head_index_is_answerable():
    status, hdrs, body = _http("/", method="HEAD")
    assert status == 200, status
    assert "content-security-policy" in hdrs and hdrs.get("x-content-type-options") == "nosniff"
    assert body == b""


def test_static_serves_known_files():
    for name in ("reader.css", "reader.js"):
        status, hdrs, data = _http(f"/static/{name}")
        assert status == 200, (name, status)
        assert data, name
        assert (ROOT / "book2real" / "web" / name).read_bytes() == data


def test_static_whitelist_is_gone():
    """Any file inside web/ is served, not just reader.css/reader.js."""
    candidates = ["app.js", "index.html", "style.css", "manifest.json",
                  "qa.js", "voice.js", "tts.js", "offline.js"]
    name = next((c for c in candidates if (ROOT / "book2real" / "web" / c).is_file()), None)
    if name is None:
        return "skip"
    status, hdrs, data = _http(f"/static/{name}")
    assert status == 200, (name, status)
    assert data == (ROOT / "book2real" / "web" / name).read_bytes()
    assert hdrs.get("x-content-type-options") == "nosniff"


def test_static_traversal_is_forbidden():
    for target in ("/static/../book2real/reader_app.py",
                   "/static/..%2fbook2real%2freader_app.py",
                   "/static/%2e%2e/%2e%2e/Windows/win.ini"):
        status = _raw(target)
        assert status in (403, 404), (target, status)


def test_sw_js_has_service_worker_scope():
    if not (ROOT / "book2real" / "web" / "sw.js").is_file():
        return "skip"                 # web-agent writes it in parallel; not our file
    status, hdrs, data = _http("/static/sw.js")
    assert status == 200, status
    assert hdrs.get("service-worker-allowed") == "/", hdrs
    assert b"CACHE_V" in data


def test_books_index():
    status, _h, payload = _json_body("/api/books")
    assert status == 200
    books = payload.get("books") or []
    assert books
    for b in books:
        assert b["id"] and b["title"] and b["pages"] > 0


def test_book_files_lists_reader_tree():
    bid = _first_book()
    status, _h, payload = _json_body(f"/api/books/{bid}/files")
    assert status == 200, status
    assert payload["book"] == bid
    assert payload["files"], payload
    assert payload["bytes"] > 0
    for f in payload["files"]:
        assert f["path"].startswith("reader/"), f
        assert f["bytes"] > 0, f
    assert "reader/book.json" in {f["path"] for f in payload["files"]}


def test_book_files_unknown_book_is_404():
    status, _h, payload = _json_body("/api/books/__does_not_exist__/files")
    assert status == 404, status
    assert "error" in payload


def test_book_files_traversal_is_rejected():
    for target in (f"/api/books/..%2f..%2f..%2fWindows/files",
                   f"/api/books/%2e%2e/files"):
        status = _raw(target)
        assert status in (403, 404), (target, status)


def test_tts_probe_does_not_synthesise():
    status, hdrs, payload = _json_body("/api/tts?probe=1&text=" + urllib.parse.quote("你好"))
    assert status == 200, status
    assert payload["ok"] is True
    assert payload["engine"] in ("edge", "sapi", "none"), payload
    assert isinstance(payload["cached"], bool)
    assert payload["chars"] == 2, payload
    assert isinstance(payload["key"], str) and len(payload["key"]) == 20, payload
    assert hdrs.get("x-tts-cache") in ("hit", "miss"), hdrs
    assert hdrs.get("x-tts-chars") == "2", hdrs


def test_tts_probe_truncates_at_400_chars():
    text = "啊" * 500
    status, hdrs, payload = _json_body("/api/tts?probe=1&text=" + urllib.parse.quote(text))
    assert status == 200
    assert payload["chars"] == 400, payload
    assert hdrs.get("x-tts-chars") == "400"


def test_tts_bare_probe_is_200():
    """The eval gate probes /api/tts?probe=1 with no text; it must be a 200."""
    status, hdrs, payload = _json_body("/api/tts?probe=1")
    assert status == 200, status
    assert payload["ok"] is True, payload
    assert payload["chars"] == 0, payload
    assert payload["cached"] is False, payload
    assert payload["engine"] in ("edge", "sapi", "none"), payload
    assert isinstance(payload["key"], str) and len(payload["key"]) == 20, payload
    assert hdrs.get("x-tts-cache") == "miss", hdrs
    assert hdrs.get("x-tts-chars") == "0", hdrs


def test_tts_empty_text_is_400():
    status, _h, _d = _http("/api/tts?text=")
    assert status == 400, status


def test_tts_cache_hit_returns_audio():
    text = f"缓存探针 voice-agent {os.getpid()}"
    hit = T.cached_path(text)
    if hit is not None:
        hit.unlink(missing_ok=True)
    target = T.CACHE / f"{T.cache_key(text)}.mp3"
    target.parent.mkdir(parents=True, exist_ok=True)
    fake = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 900
    target.write_bytes(fake)
    STATE["fake"] = target
    status, hdrs, data = _http("/api/tts?text=" + urllib.parse.quote(text))
    assert status == 200, status
    assert hdrs.get("content-type", "").startswith("audio/mpeg"), hdrs
    assert hdrs.get("x-tts-cache") == "hit", hdrs
    assert hdrs.get("x-tts-chars") == str(len(text)), hdrs
    assert data == fake, (len(data), len(fake))


def test_tts_503_shape_on_synth_failure():
    """A failing synth must be JSON 503 {"error","fallback":"l1"}, never HTML 500.

    Unit level on purpose: forcing the real edge-tts/SAPI path to fail would
    need the network or PowerShell, both banned in this test.
    """
    from book2real import reader_app, tts

    captured: dict = {}

    class Stub:
        command = "GET"

        def _json(self, obj, code=200, extra=None):
            captured.update(code=code, obj=obj, extra=extra or {})

        def _send(self, *_a, **_k):
            raise AssertionError("no audio may be sent on failure")

        def _error(self, code, msg):
            captured.update(code=code, obj={"error": msg}, extra={})

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    original = tts.synth
    tts.synth = boom
    try:
        reader_app.Handler._tts(Stub(), {"text": ["你好"]})
    finally:
        tts.synth = original
    assert captured["code"] == 503, captured
    assert captured["obj"].get("fallback") == "l1", captured
    assert "error" in captured["obj"], captured
    assert captured["extra"].get("X-TTS-Chars") == "2", captured


def test_tts_cache_key_is_stable_and_shared():
    a = T.cache_key("你好")
    assert a == T.cache_key(" 你好 ")
    assert len(a) == 20 and all(c in "0123456789abcdef" for c in a)
    assert a != T.cache_key("你好", voice="zh-CN-YunxiNeural")
    assert a != T.cache_key("你好", rate="+0%")
    assert a != T.cache_key("再见")


def test_tts_cached_path_and_engine():
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="b2r_tts_cached_"))
    try:
        assert T.cached_path("随便", out_dir=tmp) is None
        key = T.cache_key("随便")
        (tmp / f"{key}.wav").write_bytes(b"\x00" * 600)
        hit = T.cached_path("随便", out_dir=tmp)
        assert hit is not None and hit.suffix == ".wav"
        assert T.engine_of(hit) == "sapi"
        assert T.engine_of(None) == "none"
        assert T.available_engine() in ("edge", "sapi", "none")
        info = T.probe("随便", out_dir=tmp)
        assert info["cached"] is True and info["engine"] == "sapi"
        assert info["chars"] == 2 and info["key"] == key
        assert T.cached_path("", out_dir=tmp) is None
        empty = T.probe("", out_dir=tmp)
        assert empty["chars"] == 0 and empty["cached"] is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tts_lru_prunes_from_mtime_to_two_thirds():
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="b2r_tts_lru_"))
    try:
        base = time.time() - 1000
        for i in range(5):
            p = tmp / f"k{i}.mp3"
            p.write_bytes(b"\x00" * (1024 * 1024))
            os.utime(p, (base + i, base + i))
        report = T.prune_cache(limit_mb=3, cache_dir=tmp)
        assert report["removed"] == 3, report
        assert report["before"] == 5 * 1024 * 1024, report
        assert report["after"] == 2 * 1024 * 1024, report        # 3MB * 2/3
        left = sorted(p.name for p in tmp.iterdir())
        assert left == ["k3.mp3", "k4.mp3"], left                # only the newest survive
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tts_cache_limit_env_parsing_and_disable():
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="b2r_tts_env_"))
    old = os.environ.get("BOOK2REAL_TTS_MB")
    try:
        for i in range(2):
            (tmp / f"x{i}.mp3").write_bytes(b"\x00" * (1024 * 1024))
        os.environ.pop("BOOK2REAL_TTS_MB", None)
        assert T._cache_limit_mb() == T.CACHE_MB
        os.environ["BOOK2REAL_TTS_MB"] = "50"
        assert T._cache_limit_mb() == 50.0
        os.environ["BOOK2REAL_TTS_MB"] = "nonsense"
        assert T._cache_limit_mb() == T.CACHE_MB
        report = T.prune_cache(limit_mb=0, cache_dir=tmp)        # 0 disables eviction
        assert report["removed"] == 0 and report["after"] == 2 * 1024 * 1024, report
        assert len(list(tmp.iterdir())) == 2
        original = T.prune_cache
        T.prune_cache = lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom"))
        try:
            failed = T.enforce_cache_limit()
        finally:
            T.prune_cache = original
        assert "error" in failed                                 # never raises, reports
    finally:
        if old is None:
            os.environ.pop("BOOK2REAL_TTS_MB", None)
        else:
            os.environ["BOOK2REAL_TTS_MB"] = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_unknown_api_is_json_404():
    status, _h, payload = _json_body("/api/nope")
    assert status == 404, status
    assert "error" in payload


def test_ask_never_500():
    body = json.dumps({"book": "charlie_lola", "page": "p03", "mode": "free",
                       "query": "这是什么？", "history": []}).encode("utf-8")
    status, _h, payload = _json_body("/api/ask", "POST", body)
    assert status == 200, status
    assert isinstance(payload.get("answer"), str) and payload["answer"], payload
    assert payload.get("source") in ("rule", "llm"), payload
    assert payload.get("fallback") in ALLOWED_FALLBACKS, payload
    assert isinstance(payload.get("citations"), list), payload
    assert isinstance(payload.get("socratic"), dict), payload
    assert isinstance(payload.get("latency_ms"), (int, float)), payload
    if not QA_FILE.exists():
        assert payload["fallback"] == "qa_unavailable", payload


def test_ask_with_garbled_body_still_200():
    status, _h, payload = _json_body("/api/ask", "POST", b"{not json{")
    assert status == 200, status
    assert isinstance(payload.get("answer"), str) and payload["answer"]
    assert payload.get("fallback") in ALLOWED_FALLBACKS, payload


def test_books_traversal_is_forbidden():
    for target in ("/books/../../Windows/win.ini",
                   "/books/..%2f..%2fWindows/win.ini",
                   "/books/charlie_lola/../../../Windows/win.ini",
                   "/books/../booksX/evil.txt",          # the old startswith() hole
                   "/books/../book2real/reader_app.py"):  # sibling package, never served
        assert _raw(target) in (403, 404), target


def test_media_traversal_is_forbidden():
    for target in ("/media/../../Windows/win.ini",
                   "/media/..%2f..%2fWindows/win.ini"):
        assert _raw(target) in (403, 404), target


def test_api_book_query_traversal_is_rejected():
    for value in ("../../Windows/win.ini", "..%2f..%2fWindows%2fwin.ini"):
        status, _h, payload = _json_body("/api/book?book=" + urllib.parse.quote(value))
        assert status in (403, 404), (value, status)
        assert "error" in payload


def test_startup_prints_urls():
    log = _log_tail(8000)
    port = STATE["port"]
    assert "[reader]" in log, log
    assert f"127.0.0.1:{port}/" in log, log
    assert "--host 0.0.0.0" in log, log


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
