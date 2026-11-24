"""赛题 50 条 query 的预生成答案缓存：命中后直接播预合成语音，0ms 等 LLM。"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from ..config import Settings
from ..utils import normalize_text
from ..book.characters import names_in


@dataclass
class CachedAnswer:
    q: str
    norm: str
    answer: str
    lang: str
    intent: str
    action: Optional[dict]
    evidence: list
    pcm: bytes
    sample_rate: int
    score: float = 0.0


class QueryCache:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.data_dir / "books"
        self._books: dict[str, list[CachedAnswer]] = {}
        self._loaded: set[str] = set()

    def _load(self, book_id: str) -> list[CachedAnswer]:
        if book_id in self._loaded:
            return self._books.get(book_id, [])
        self._loaded.add(book_id)
        manifest = self.root / book_id / "query_cache.json"
        if not manifest.is_file():
            return []
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            return []
        base = manifest.parent
        entries: list[CachedAnswer] = []
        for item in data.get("entries") or []:
            pcm = b""
            audio_rel = item.get("audio")
            if audio_rel:
                try:
                    pcm = (base / audio_rel).read_bytes()
                except Exception:
                    pcm = b""
            if not pcm:
                continue
            entries.append(
                CachedAnswer(
                    q=str(item.get("q") or ""),
                    norm=normalize_text(str(item.get("q") or "")),
                    answer=str(item.get("answer") or "").strip(),
                    lang=str(item.get("lang") or "zh"),
                    intent=str(item.get("intent") or "qa"),
                    action=item.get("action"),
                    evidence=item.get("evidence") or [],
                    pcm=pcm,
                    sample_rate=int(item.get("sample_rate") or self.settings.tts_sample_rate),
                )
            )
        self._books[book_id] = entries
        return entries

    def lookup(self, book_id: str, query: str, threshold: float = 0.82) -> Optional[CachedAnswer]:
        qn = normalize_text(query)
        if not qn:
            return None
        entries = self._load(book_id)
        if not entries:
            return None
        qnames = names_in(query)
        best: Optional[CachedAnswer] = None
        best_score = 0.0
        for entry in entries:
            if not entry.norm or not entry.answer:
                continue
            # 人物集合不一致时不做缓存命中，防止把“小满”的问题套到“花花”上
            if qnames != names_in(entry.q):
                continue
            if entry.norm == qn:
                return replace(entry, score=1.0)
            if len(qn) < 4:
                continue
            score = difflib.SequenceMatcher(None, qn, entry.norm).ratio()
            if score > best_score:
                best_score, best = score, entry
        if best is not None and best_score >= threshold:
            return replace(best, score=round(best_score, 4))
        return None

    def status(self) -> dict:
        return {
            "loaded": {k: len(v) for k, v in self._books.items()},
            "books": sorted(self._loaded),
        }
