"""融合层测试：backend FastAPI 同时托管阅读器页面/旧 API 与 backend 契约。

跑法（脚本式，与 tests/ 其它文件一致）：

    py tests/test_fusion.py

也兼容 pytest（``py -m pytest tests/test_fusion.py``）。
"""
from __future__ import annotations

import atexit
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app import compat  # noqa: E402
from app.main import app  # noqa: E402

_CLIENT: TestClient | None = None


def _client() -> TestClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = TestClient(app)
        _CLIENT.__enter__()
        atexit.register(lambda: _CLIENT.__exit__(None, None, None))
    return _CLIENT


def test_reader_page_has_csp():
    r = _client().get("/")
    assert r.status_code == 200, r.status_code
    assert "default-src 'self'" in r.headers.get("content-security-policy", "")
    assert "nosniff" in r.headers.get("x-content-type-options", "")


def test_static_serves_any_file_in_web_dir():
    assert _client().get("/static/reader.css").status_code == 200
    assert _client().get("/static/qa.js").status_code == 200
    assert _client().get("/static/../book2real/qa.py").status_code in (403, 404)


def test_books_list_has_reader_fields():
    payload = _client().get("/api/books").json()
    books = payload["books"]
    assert books, "backend 还没有书；先跑 backend/tools/ingest_competition.py"
    first = books[0]
    assert {"id", "title", "pages", "page_count"} <= set(first), first
    assert first["pages"] == first["page_count"]


def test_api_book_serves_reader_package():
    books = _client().get("/api/books").json()["books"]
    r = _client().get("/api/book", params={"book": books[0]["id"]})
    assert r.status_code == 200, r.status_code
    data = r.json()
    assert data["book_id"] == books[0]["id"]
    assert data["pages"] and "text" in data["pages"][0]


def test_api_book_rejects_traversal():
    for value in ("../../Windows/win.ini", "..%2f..%2fWindows%2fwin.ini"):
        r = _client().get("/api/book", params={"book": value})
        assert r.status_code in (403, 404), (value, r.status_code)


def test_tts_probe_does_not_synthesise():
    r = _client().get("/api/tts", params={"text": "你好", "probe": "1"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is True
    assert payload["chars"] == 2


def test_tts_empty_text_is_400():
    assert _client().get("/api/tts").status_code == 400


def test_compat_normalizes_legacy_ask_payload():
    payload = compat.normalize_ask_payload(
        {
            "book": "cn01",
            "page": "p03",
            "query": "他们在搭什么？",
            "entity": {"name": "积木城堡", "desc": "已经搭到九块"},
            "box": [0.58, 0.72, 0.08, 0.04],
            "history": [{"q": "a", "a": "b"}],
        }
    )
    assert payload["book_id"] == "cn01"
    assert payload["text"] == "他们在搭什么？"
    assert payload["page"] == 3
    element = payload["element"]
    assert element["name"] == "积木城堡"
    assert [round(v, 6) for v in element["bbox"]] == [0.58, 0.72, 0.66, 0.76]


def test_compat_decorates_backend_answer():
    out = compat.decorate_answer(
        {
            "answer": "绘本里说：一起搭城堡。",
            "evidence": [{"page": 2, "text": "我们一起搭城堡", "score": 9.0}],
            "timings": {"first_audio": 631.2},
        },
        {"page": 3, "book_id": "cn01"},
        llm_name="extractive",
    )
    assert out["citations"][0]["page"] == "p02"
    assert out["source"] == "rule"
    assert out["page"] == "p03"
    assert out["latency_ms"] == 631.2
    assert out["socratic"]["directive"] == "none"


def test_reader_package_text_matches_backend():
    """两个数据源必须同源：阅读器包正文来自 backend 的 story_text。"""
    import json

    books = _client().get("/api/books").json()["books"]
    book_id = books[0]["id"]
    backend_path = ROOT / "data" / "books" / book_id / "book.json"
    reader_path = ROOT / "books" / book_id / "reader" / "book.json"
    if not (backend_path.is_file() and reader_path.is_file()):
        return  # 没入库/没重建时跳过（CI 里只跑契约测试）
    backend = json.loads(backend_path.read_text(encoding="utf-8"))
    reader = json.loads(reader_path.read_text(encoding="utf-8"))
    by_source = {p["source_page"]: p for p in reader["pages"] if p.get("part", 1) == 1}
    checked = 0
    for page in backend["pages"]:
        target = by_source.get(page["no"])
        source = (page.get("story_text") or "").strip()
        if not target or not source:
            continue
        if target.get("kind") == "cover":
            continue  # 封面正文由 curation 决定，不跟 backend 的版权页文字比
        stem = source[:10]
        assert stem in target["text"], (page["no"], stem, target["text"][:40])
        checked += 1
        if checked >= 3:
            break
    assert checked or not by_source, "阅读器包与 backend 数据没有可比页面"


def test_unknown_api_is_json_404():
    r = _client().get("/api/nope")
    assert r.status_code == 404
    assert r.headers.get("content-type", "").startswith("application/json")


if __name__ == "__main__":
    ok = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print(f"ok  {name}")
    print(f"{ok} tests passed")
