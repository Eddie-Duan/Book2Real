from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.pipeline.query_cache import QueryCache


def _settings(tmp_path: Path) -> Settings:
    cfg = Settings(data_dir=tmp_path)
    cfg.data_dir = tmp_path
    cfg.ensure_dirs()
    return cfg


def _write_cache(tmp_path: Path) -> None:
    book = tmp_path / "books" / "demo"
    (book / "query_cache").mkdir(parents=True)
    (book / "query_cache" / "000.pcm").write_bytes(b"\x00\x00" * 100)
    manifest = {
        "version": 1,
        "book_id": "demo",
        "entries": [
            {
                "q": "小满为什么难过？",
                "answer": "不是小满，是花花不开心，想和小满一起过生日。",
                "lang": "zh",
                "intent": "qa",
                "action": None,
                "evidence": [],
                "audio": "query_cache/000.pcm",
                "sample_rate": 24000,
            }
        ],
    }
    (book / "query_cache.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_query_cache_exact_and_fuzzy(tmp_path: Path):
    _write_cache(tmp_path)
    cache = QueryCache(_settings(tmp_path))
    hit = cache.lookup("demo", "小满为什么难过？")
    assert hit is not None
    assert hit.answer.startswith("不是小满")
    assert hit.pcm

    fuzzy = cache.lookup("demo", "小满为什么难过")
    assert fuzzy is not None


def test_query_cache_refuses_name_mismatch(tmp_path: Path):
    _write_cache(tmp_path)
    cache = QueryCache(_settings(tmp_path))
    assert cache.lookup("demo", "花花为什么难过？") is None


def test_query_cache_missing_manifest(tmp_path: Path):
    cache = QueryCache(_settings(tmp_path))
    assert cache.lookup("demo", "小满为什么难过？") is None
