"""SQLite 持久化：会话、多模态消息、每回合延迟指标。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from ..config import Settings


class Store:
    def __init__(self, settings: Settings) -> None:
        settings.ensure_dirs()
        self.path = settings.db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY,
                  book_id TEXT,
                  created_at REAL,
                  updated_at REAL
                );
                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_id TEXT,
                  role TEXT,
                  text TEXT,
                  lang TEXT,
                  page INTEGER,
                  scene_t REAL,
                  element TEXT,
                  image TEXT,
                  action_json TEXT,
                  created_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS turns (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_id TEXT,
                  book_id TEXT,
                  created_at REAL,
                  speech_end_ms REAL,
                  first_audio_ms REAL,
                  done_ms REAL,
                  asr_ms REAL,
                  llm_ms REAL,
                  tts_ms REAL,
                  used_filler INTEGER DEFAULT 0,
                  warmup INTEGER DEFAULT 0,
                  query TEXT,
                  answer TEXT,
                  metrics_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_turns_created ON turns(created_at);
                """
            )
            self._conn.commit()

    def ensure_session(self, session_id: str, book_id: str = "") -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id,book_id,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET book_id=excluded.book_id, updated_at=excluded.updated_at",
                (session_id, book_id, now, now),
            )
            self._conn.commit()

    def add_message(
        self,
        session_id: str,
        role: str,
        text: str = "",
        lang: str = "",
        page: Optional[int] = None,
        scene_t: Optional[float] = None,
        element: str = "",
        image: str = "",
        action: Optional[dict] = None,
    ) -> None:
        self.ensure_session(session_id)
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(session_id,role,text,lang,page,scene_t,element,image,action_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (session_id, role, text, lang, page, scene_t, element, image, json.dumps(action or {}, ensure_ascii=False), time.time()),
            )
            self._conn.commit()

    def history(self, session_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)
            ).fetchall()
        out = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["action"] = json.loads(item.pop("action_json") or "{}")
            except json.JSONDecodeError:
                item["action"] = {}
            out.append(item)
        return out

    def recent_context(self, session_id: str, turns: int = 8) -> list[dict]:
        history = self.history(session_id, limit=turns * 2)
        return [{"role": m["role"], "content": m["text"]} for m in history if m.get("text")]

    def record_turn(
        self,
        session_id: str,
        book_id: str,
        speech_end_ms: float,
        first_audio_ms: Optional[float],
        done_ms: Optional[float],
        asr_ms: float,
        llm_ms: float,
        tts_ms: float,
        used_filler: bool,
        warmup: bool,
        query: str,
        answer: str,
        metrics: Optional[dict] = None,
    ) -> None:
        self.ensure_session(session_id, book_id)
        with self._lock:
            self._conn.execute(
                "INSERT INTO turns(session_id,book_id,created_at,speech_end_ms,first_audio_ms,done_ms,asr_ms,llm_ms,tts_ms,"
                "used_filler,warmup,query,answer,metrics_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    book_id,
                    time.time(),
                    speech_end_ms,
                    first_audio_ms,
                    done_ms,
                    asr_ms,
                    llm_ms,
                    tts_ms,
                    1 if used_filler else 0,
                    1 if warmup else 0,
                    query,
                    answer,
                    json.dumps(metrics or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def latency_stats(self, budget_ms: int = 2000, session_id: str = "", book_id: str = "") -> dict:
        where = ["warmup=0", "first_audio_ms IS NOT NULL"]
        args: list = []
        if session_id:
            where.append("session_id=?")
            args.append(session_id)
        if book_id:
            where.append("book_id=?")
            args.append(book_id)
        sql = "SELECT first_audio_ms-speech_end_ms AS total, asr_ms, llm_ms, tts_ms, used_filler FROM turns WHERE " + " AND ".join(where)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        values = sorted(float(r["total"]) for r in rows if r["total"] is not None)
        n = len(values)
        if not n:
            return {"count": 0, "budget_ms": budget_ms, "hit_ratio": 0.0, "p50": 0, "p90": 0, "p95": 0, "filler_ratio": 0.0}

        def pct(p: float) -> float:
            idx = min(n - 1, max(0, int(round((n - 1) * p))))
            return values[idx]

        filler = sum(1 for r in rows if r["used_filler"])
        return {
            "count": n,
            "budget_ms": budget_ms,
            "hit_ratio": round(sum(1 for v in values if v <= budget_ms) / n, 4),
            "p50": round(pct(0.50), 1),
            "p90": round(pct(0.90), 1),
            "p95": round(pct(0.95), 1),
            "min": round(values[0], 1),
            "max": round(values[-1], 1),
            "filler_ratio": round(filler / n, 4),
            "avg_asr_ms": round(sum(float(r["asr_ms"] or 0) for r in rows) / n, 1),
            "avg_llm_ms": round(sum(float(r["llm_ms"] or 0) for r in rows) / n, 1),
            "avg_tts_ms": round(sum(float(r["tts_ms"] or 0) for r in rows) / n, 1),
        }

    def recent_turns(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
            except json.JSONDecodeError:
                item["metrics"] = {}
            if item.get("first_audio_ms") and item.get("speech_end_ms"):
                item["first_audio_after_speech_ms"] = round(item["first_audio_ms"] - item["speech_end_ms"], 1)
            out.append(item)
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()
