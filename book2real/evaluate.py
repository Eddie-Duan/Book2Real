"""Offline evaluation harness for the query stack (script -> ASR -> retrieval -> QA).

    python -m book2real evaluate --book books/cn01 --audio "<official audio dir>" \
        [--limit 5] [--asr auto|none] [--labels labels.json] [--json out.json]

What it measures, and what it refuses to measure:

* **ASR** — optional Windows SAPI (``System.Speech.Recognition``) backend, invoked
  through PowerShell.  There is no ASR dependency for the product; when the
  recognizer is missing, errors or times out the item degrades to ``skipped``
  and the run continues.  CER is reported only for successfully recognized
  items (Chinese: characters, English: words).
* **Retrieval** — the query is scored against ``reader/book.json``
  ``pages[].text/scene/spots[].name/desc`` with Chinese 2-gram / English word
  IDF coverage (reusing :func:`book2real.qa._bigrams`).  Top-1 page, its
  continuous score and the runner-ups are reported; a zero-scoring
  ``find``/``highlight`` is flagged ``no_match`` instead of being dressed up.
* **QA** — :func:`book2real.qa.answer` is called on the top-1 page.  LLM keys
  are masked for the duration of the run, so the numbers describe the offline
  rule path and the harness itself never needs the network.
* **Accuracy** — only computed when ``--labels {n: page_id}`` is supplied.
  Without human labels the report says ``accuracy: null`` and reports ASR CER,
  top-1 score distribution, first-response latency percentiles and coverage.

Every per-item failure is contained; the process exits 0 with a report even if
zero items could be evaluated.  Only missing inputs (book package, audio dir,
script file, labels file) abort with a clear :class:`EvaluateError`.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import qa
from .ingest import read_script

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / "纸书智伴企业预选赛绘本素材" / "绘本素材"
RUNS = ROOT / "runs"
EVAL_KINDS = ("ask", "find", "highlight", "click")
NO_MATCH_EPS = 0.02
ASR_CULTURES = {"zh": "zh-CN", "en": "en-US"}
DEFAULT_ASR_TIMEOUT = 30.0
LABEL_JSON_MAX = 4 * 1024 * 1024

_FALLBACKS = {None, "no_llm_key", "llm_error", "no_evidence", "qa_unavailable"}


class EvaluateError(RuntimeError):
    """A missing/unreadable input the user can fix (never a per-item failure)."""


# --------------------------------------------------------------------------- #
# small IO helpers
# --------------------------------------------------------------------------- #
def _read_json(path: Path, what: str) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise EvaluateError(f"{what} 不存在：{path}") from None
    except OSError as exc:
        raise EvaluateError(f"{what} 读取失败：{path}（{exc}）") from None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise EvaluateError(f"{what} 不是合法 JSON：{path}（{exc}）") from None
    if not isinstance(data, dict):
        raise EvaluateError(f"{what} 顶层必须是对象：{path}")
    return data


def _book_dir(raw: str) -> Path:
    cand = Path(raw)
    if not cand.is_absolute():
        cand = Path.cwd() / cand
    if not cand.is_dir():
        alt = ROOT / "books" / raw
        if alt.is_dir():
            cand = alt
        else:
            raise EvaluateError(f"书目录不存在：{raw}（也试过 {alt}）")
    return cand.resolve()


def _language(book: dict, reader: dict) -> str:
    for src in (reader, book):
        lang = str(src.get("language") or "").strip().lower()
        if lang.startswith("en"):
            return "en"
        if lang.startswith("zh") or lang in ("cn", "chinese"):
            return "zh"
    return "zh"


def script_for(book_id: str) -> Path:
    """Official script for ``cn01`` -> ``中文绘本01.txt`` (``en01`` -> 英文...)."""
    m = re.fullmatch(r"(cn|en)(\d+)", book_id)
    if not m:
        raise EvaluateError(
            f"无法从 book id 推断脚本编号：{book_id!r}；请用 --script 指定录制脚本")
    prefix = "中文绘本" if m.group(1) == "cn" else "英文绘本"
    return MATERIALS / f"{prefix}{m.group(2)}.txt"


def _resolve_script(book_id: str, override: str | None) -> Path:
    path = Path(override) if override else script_for(book_id)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        raise EvaluateError(f"录制脚本不存在：{path}")
    return path


def _audio_index(audio_dir: Path) -> dict[int, Path]:
    """``06.wav`` -> ``{6: path}``; duplicate numbers keep the first by name."""
    out: dict[int, Path] = {}
    try:
        entries = sorted(audio_dir.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        raise EvaluateError(f"音频目录不可读：{audio_dir}（{exc}）") from None
    for entry in entries:
        if not entry.is_file():          # skip sub-dirs such as zh/, raw/
            continue
        m = re.fullmatch(r"(\d+)", entry.stem)
        if not m:
            continue
        n = int(m.group(1))
        if n not in out:
            out[n] = entry
    return out


def _labels_from(path: str | None) -> dict[int, str] | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.is_file():
        raise EvaluateError(f"标注文件不存在：{p}")
    if p.stat().st_size > LABEL_JSON_MAX:
        raise EvaluateError(f"标注文件过大（>{LABEL_JSON_MAX} 字节）：{p}")
    data = _read_json(p, "标注文件")
    labels: dict[int, str] = {}
    for key, value in data.items():
        try:
            n = int(key)
        except (TypeError, ValueError):
            raise EvaluateError(f"标注文件的键必须是脚本编号（整数）：{key!r}") from None
        if not isinstance(value, str) or not value.strip():
            raise EvaluateError(f"标注 {key} 的页码必须是非空字符串")
        labels[n] = value.strip()
    return labels


# --------------------------------------------------------------------------- #
# text normalisation + CER (Chinese chars / English words)
# --------------------------------------------------------------------------- #
_QUOTES = "“”\"'‘’「」『』《》"


def script_query(row: dict) -> str:
    text = str(row.get("text") or "").strip()
    text = text.translate({ord(ch): None for ch in _QUOTES})
    return re.sub(r"\s+", " ", text).strip()


def _norm_zh(text: str) -> list[str]:
    low = (text or "").lower()
    return [ch for ch in low if "\u4e00" <= ch <= "\u9fff" or ch.isascii() and ch.isalnum()]


def _norm_words(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-z\u4e00-\u9fff]+", (text or "").lower()) if w]


def _levenshtein(a: list[str], b: list[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str, language: str = "zh") -> float | None:
    """Character (zh) / word (en) error rate; ``None`` for an empty reference."""
    ref = _norm_words(reference) if language == "en" else _norm_zh(reference)
    hyp = _norm_words(hypothesis) if language == "en" else _norm_zh(hypothesis)
    if not ref:
        return None
    return round(_levenshtein(ref, hyp) / len(ref), 4)


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile; ``None`` when there is nothing to rank."""
    if not values:
        return None
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return round(xs[0], 2)
    rank = (len(xs) - 1) * max(0.0, min(100.0, pct)) / 100.0
    lo = int(math.floor(rank))
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return round(xs[lo] + (xs[hi] - xs[lo]) * frac, 2)


# --------------------------------------------------------------------------- #
# retrieval: page text / scene / spot names + descriptions
# --------------------------------------------------------------------------- #
def _spot_names(page: dict) -> list[str]:
    names = []
    for spot in page.get("spots") or []:
        if isinstance(spot, dict):
            name = str(spot.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def _page_text(page: dict) -> str:
    parts = [str(page.get("text") or ""), str(page.get("scene") or "")]
    for spot in page.get("spots") or []:
        if isinstance(spot, dict):
            parts.append(str(spot.get("name") or ""))
            parts.append(str(spot.get("desc") or ""))
    return " ".join(p for p in parts if p)


def retrieve(pages: list[dict], query: str, top_k: int = 3) -> list[dict]:
    """IDF-weighted bigram/word coverage over reader pages, best first."""
    query = (query or "").strip()
    grams = list(dict.fromkeys(qa._bigrams(query)))
    if not grams or not pages:
        return []
    docs = [_page_text(p) for p in pages]
    doc_grams = [qa._bigrams(d) for d in docs]
    n = len(pages)
    df = {g: sum(1 for grams_i in doc_grams if g in grams_i) for g in grams}
    idf = {g: math.log(1.0 + (n + 1) / (1.0 + df[g])) for g in grams}
    total = sum(idf.values()) or 1.0
    scored: list[tuple[float, str]] = []
    for page, doc_grams_i, doc in zip(pages, doc_grams, docs):
        score = sum(idf[g] for g in grams if g in doc_grams_i) / total
        hit_names = [nm for nm in _spot_names(page) if len(nm) >= 2 and nm in query]
        if hit_names and any(nm in doc for nm in hit_names):
            score = max(score, 0.6)          # a named spot beats a diluted summary
        pid = str(page.get("id") or "")
        scored.append((round(min(score, 1.0), 4), pid))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [{"page": pid, "score": score} for score, pid in scored[:top_k]
            if score > 0.0] or [{"page": scored[0][1], "score": scored[0][0]}]


# --------------------------------------------------------------------------- #
# optional Windows SAPI ASR (PowerShell + System.Speech)
# --------------------------------------------------------------------------- #
_ASR_PS1 = r'''
param([string]$Path = "", [string]$Culture = "zh-CN", [switch]$Probe)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
    Add-Type -AssemblyName System.Speech
    $recs = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
    if ($Probe) {
        foreach ($r in $recs) { [Console]::Out.WriteLine($r.Culture.Name + "|" + $r.Id) }
        exit 0
    }
    $match = $recs | Where-Object { $_.Culture.Name -eq $Culture }
    if (-not $match) {
        [Console]::Out.WriteLine("__ASR_UNAVAILABLE__")
        exit 0
    }
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine -ArgumentList ([System.Globalization.CultureInfo]$Culture)
    $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
    $engine.SetInputToWaveFile($Path)
    $result = $engine.Recognize()
    if ($result) { [Console]::Out.WriteLine($result.Text) }
    else { [Console]::Out.WriteLine("__ASR_NONE__") }
    $engine.Dispose()
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
'''

_CULTURE_CACHE: dict[str, bool] = {}


def _powershell() -> str | None:
    for name in ("powershell", "pwsh"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def _run_ps(args: list[str], timeout: float) -> tuple[str, str]:
    exe = _powershell()
    if not exe:
        return "", "asr_unavailable"
    fd, tmp = tempfile.mkstemp(suffix=".ps1", prefix="b2r_asr_")
    os.close(fd)
    try:
        Path(tmp).write_text(_ASR_PS1, encoding="ascii")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", tmp, *args],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=max(1.0, float(timeout)), creationflags=flags,
            )
        except subprocess.TimeoutExpired:
            return "", "asr_timeout"
        except OSError:
            return "", "asr_error"
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if proc.returncode != 0:
        return "", "asr_error"
    out = (proc.stdout or "").strip().lstrip("\ufeff").strip()
    if out == "__ASR_UNAVAILABLE__":
        return "", "asr_unavailable"
    if out in ("", "__ASR_NONE__"):
        return "", "asr_empty"
    return out, ""


def recognizer_available(culture: str) -> bool:
    if culture not in _CULTURE_CACHE:
        out, reason = _run_ps(["-Culture", culture, "-Probe"], 20.0)
        ok = reason == "" and any(
            line.split("|", 1)[0].strip() == culture for line in out.splitlines())
        _CULTURE_CACHE[culture] = ok
    return _CULTURE_CACHE[culture]


def recognize_wav(path: Path, culture: str,
                  timeout: float = DEFAULT_ASR_TIMEOUT) -> tuple[str, str]:
    """-> (transcript, reason).  reason is "" on success, else a skip reason."""
    return _run_ps(["-Path", str(path), "-Culture", culture], timeout)


# --------------------------------------------------------------------------- #
# QA (offline rule path)
# --------------------------------------------------------------------------- #
@contextmanager
def _offline_qa(book_dir: Path):
    """Mask LLM credentials and point qa at this book's parent directory."""
    keys = ("B2R_LLM_KEY", "DEEPSEEK_API_KEY", "LLM_API_KEY")
    saved = {key: os.environ.get(key) for key in keys}
    prev_books = qa.BOOKS
    try:
        for key in keys:
            os.environ[key] = ""          # _llm_config() -> None; load_env uses setdefault
        qa.BOOKS = str(book_dir.parent)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        qa.BOOKS = prev_books


def ask(book_id: str, page_id: str, query: str) -> dict:
    return qa.answer({"book": book_id, "page": page_id, "query": query})


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def _new_item(row: dict, audio_path: Path | None) -> dict:
    return {
        "n": int(row["n"]),
        "script": script_query(row),
        "kind": row["kind"],
        "audio": audio_path.name if audio_path else "",
        "transcript": None,
        "cer": None,
        "top_page": None,
        "score": None,
        "candidates": [],
        "no_match": False,
        "answer": "",
        "latency_ms": None,
        "asr_ms": None,
        "qa_ms": None,
        "eval_ms": None,
        "source": None,
        "fallback": None,
        "status": "skipped",
        "reason": "",
    }


def evaluate(book: str, audio: str, script: str | None = None,
             limit: int | None = None, asr: str = "auto",
             labels: str | None = None) -> dict:
    """Run the whole evaluation and return the report dict (never raises per item)."""
    if asr not in ("auto", "none"):
        raise EvaluateError(f"--asr 只能是 auto 或 none：{asr!r}")
    if limit is not None and int(limit) <= 0:
        raise EvaluateError(f"--limit 必须是正整数：{limit}")

    book_dir = _book_dir(book)
    book_json = _read_json(book_dir / "book.json", "book.json")
    reader = _read_json(book_dir / "reader" / "book.json", "reader/book.json")
    book_id = str(reader.get("book_id") or book_json.get("book_id") or book_dir.name)
    pages = [p for p in (reader.get("pages") or []) if isinstance(p, dict)]
    if not pages:
        raise EvaluateError(f"reader/book.json 里没有任何页面：{book_dir / 'reader' / 'book.json'}")

    language = _language(book_json, reader)
    culture = ASR_CULTURES.get(language, "zh-CN")
    asr_timeout = float(os.environ.get("B2R_ASR_TIMEOUT") or DEFAULT_ASR_TIMEOUT)

    script_path = _resolve_script(book_id, script)
    rows = read_script(script_path)
    audio_dir = Path(audio)
    if not audio_dir.is_absolute():
        audio_dir = Path.cwd() / audio_dir
    if not audio_dir.is_dir():
        raise EvaluateError(f"音频目录不存在：{audio_dir}")
    audio_index = _audio_index(audio_dir)
    label_map = _labels_from(labels)

    selected = [r for r in rows if r.get("kind") in EVAL_KINDS]
    if limit is not None:
        selected = selected[:int(limit)]
    transport = sum(1 for r in rows if r.get("kind") == "transport")

    available = None
    if asr == "auto":
        available = recognizer_available(culture)

    items: list[dict] = []
    with _offline_qa(book_dir):
        for row in selected:
            audio_path = audio_index.get(int(row["n"]))
            item = _new_item(row, audio_path)
            query = script_query(row)
            item["_query"] = query
            item_started = time.perf_counter()
            try:
                if audio_path is None:
                    item["reason"] = "no_audio"
                elif asr == "none":
                    item["transcript"] = None
                    item["cer"] = None
                elif not available:
                    item["reason"] = "asr_unavailable"
                else:
                    started = time.perf_counter()
                    text, reason = recognize_wav(audio_path, culture, asr_timeout)
                    item["asr_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
                    if reason:
                        item["reason"] = reason
                    else:
                        item["transcript"] = text
                        item["cer"] = cer(item["script"], text, language)
                        query = text
                        item["_query"] = query
                if item["reason"]:
                    items.append(item)
                    continue

                candidates = retrieve(pages, query)
                top = candidates[0]
                item["candidates"] = candidates
                item["top_page"] = top["page"]
                item["score"] = top["score"]
                item["no_match"] = bool(
                    row["kind"] in ("find", "highlight") and top["score"] <= NO_MATCH_EPS)

                t0 = time.perf_counter()
                resp = ask(book_id, top["page"], query)
                item["qa_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
                item["eval_ms"] = round((time.perf_counter() - item_started) * 1000.0, 2)
                item["answer"] = str(resp.get("answer") or "")
                item["latency_ms"] = int(resp.get("latency_ms") or 0)
                item["source"] = resp.get("source")
                item["fallback"] = resp.get("fallback") if resp.get("fallback") in _FALLBACKS else None
                item["status"] = "ok"
            except Exception as exc:               # noqa: BLE001 - one item must not abort
                item["reason"] = f"error:{type(exc).__name__}"
                item["answer"] = ""
            items.append(item)

    for item in items:
        item.pop("_query", None)
    return _report(book_id, book_dir, script_path, audio_dir, language, asr,
                   culture, available, limit, transport, label_map, items)


def _report(book_id: str, book_dir: Path, script_path: Path, audio_dir: Path,
            language: str, asr: str, culture: str, available: bool | None,
            limit: int | None, transport: int, label_map: dict[int, str] | None,
            items: list[dict]) -> dict:
    total = len(items)
    ok = [it for it in items if it["status"] == "ok"]
    success, skipped = len(ok), total - len(ok)

    cas = [it["cer"] for it in items if it["status"] == "ok" and it["cer"] is not None]
    attempted = 0 if asr == "none" else sum(
        1 for it in items if it["reason"] not in ("no_audio", "asr_unavailable"))
    scores = [it["score"] for it in ok if isinstance(it["score"], (int, float))]
    latencies = [it["latency_ms"] for it in ok if isinstance(it["latency_ms"], int)]
    walls = [it["eval_ms"] for it in ok if isinstance(it["eval_ms"], (int, float))]

    accuracy = None
    labeled = hits = 0
    if label_map is not None:
        for it in ok:
            want = label_map.get(it["n"])
            if want is None:
                continue
            labeled += 1
            hits += int(it["top_page"] == want)
        accuracy = round(hits / labeled, 4) if labeled else None

    metrics = {
        "total": total,
        "success": success,
        "skipped": skipped,
        "transport_skipped": transport,
        "coverage": round(success / total, 4) if total else None,
        "accuracy": accuracy,
        "retrieval": {
            "top1_mean_score": round(sum(scores) / len(scores), 4) if scores else None,
            "top1_p50_score": _percentile(scores, 50),
            "top1_p95_score": _percentile(scores, 95),
            "no_match": sum(1 for it in ok if it["no_match"]),
        },
        "asr": {
            "attempted": attempted,
            "recognized": len(cas),
            "failed": sum(1 for it in items if it["reason"] in
                          ("asr_unavailable", "asr_empty", "asr_error", "asr_timeout")),
            "cer_mean": round(sum(cas) / len(cas), 4) if cas else None,
            "cer_p50": _percentile(cas, 50),
            "cer_p95": _percentile(cas, 95),
        },
        "latency": {
            "n": len(latencies),
            "p50_ms": _percentile(latencies, 50),
            "p95_ms": _percentile(latencies, 95),
            "wall_p50_ms": _percentile(walls, 50),
            "wall_p95_ms": _percentile(walls, 95),
        },
    }
    return {
        "book": book_id,
        "title": str(_safe_book_title(book_dir)),
        "language": language,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "book_dir": str(book_dir),
        "audio_dir": str(audio_dir),
        "script": str(script_path),
        "asr": {
            "mode": asr,
            "backend": "windows_sapi_system.speech" if asr == "auto" else "none",
            "culture": culture,
            "available": available,
            "timeout_s": float(os.environ.get("B2R_ASR_TIMEOUT") or DEFAULT_ASR_TIMEOUT),
        },
        "limit": limit,
        "accuracy": accuracy,
        "labels": ({"provided": True, "labeled_items": labeled, "hits": hits}
                   if label_map is not None else {"provided": False}),
        "metrics": metrics,
        "items": items,
    }


def _safe_book_title(book_dir: Path) -> str:
    try:
        data = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return book_dir.name
    return str(data.get("title") or book_dir.name) if isinstance(data, dict) else book_dir.name


# --------------------------------------------------------------------------- #
# writers
# --------------------------------------------------------------------------- #
def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _cell(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    m = report["metrics"]
    a = report["asr"]
    lines = [
        f"# 查询评测报告 — {report['book']}（{report.get('title') or ''}）",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 书目录：`{report['book_dir']}`",
        f"- 录制脚本：`{report['script']}`",
        f"- 音频目录：`{report['audio_dir']}`",
        f"- ASR：`{a['mode']}`（backend={a['backend']}，culture={a['culture']}，"
        f"available={a['available']}）",
        f"- 评测条目：{m['total']}（另有 transport 行 {m['transport_skipped']} 条不计 Query）",
        f"- --limit：{report['limit']}",
        f"- **accuracy：{('null' if report['accuracy'] is None else report['accuracy'])}**"
        + ("（未提供人工标注，不计算正确率）" if report["accuracy"] is None else "（来自 --labels）"),
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 成功 / 跳过 | {m['success']} / {m['skipped']} |",
        f"| 覆盖率 coverage | {_fmt(m['coverage'])} |",
        f"| accuracy | {_fmt(report['accuracy'])} |",
        f"| 检索 top1 平均连续分 | {_fmt(m['retrieval']['top1_mean_score'])} |",
        f"| 检索 top1 p50 / p95 | {_fmt(m['retrieval']['top1_p50_score'])} / "
        f"{_fmt(m['retrieval']['top1_p95_score'])} |",
        f"| find/highlight 判为 no_match | {m['retrieval']['no_match']} |",
        f"| ASR 尝试 / 成功 / 失败 | {m['asr']['attempted']} / {m['asr']['recognized']} / "
        f"{m['asr']['failed']} |",
        f"| ASR CER 平均 / p50 / p95 | {_fmt(m['asr']['cer_mean'], 4)} / "
        f"{_fmt(m['asr']['cer_p50'], 4)} / {_fmt(m['asr']['cer_p95'], 4)} |",
        f"| 首响 p50 / p95（qa latency_ms） | {_fmt(m['latency']['p50_ms'])} / "
        f"{_fmt(m['latency']['p95_ms'])} ms |",
        f"| 首响 p50 / p95（含 ASR 的 wall） | {_fmt(m['latency']['wall_p50_ms'])} / "
        f"{_fmt(m['latency']['wall_p95_ms'])} ms |",
        "",
        "## 逐条",
        "",
        "| n | kind | audio | transcript | CER | top_page | score | latency_ms | fallback | status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for it in report["items"]:
        status = it["status"] if not it["reason"] else f"跳过({it['reason']})"
        lines.append(
            f"| {it['n']} | {it['kind']} | {_cell(it['audio']) or '—'} | "
            f"{_cell(it['transcript'] or '—')} | {_fmt(it['cer'], 4)} | "
            f"{it['top_page'] or '—'} | {_fmt(it['score'])} | "
            f"{it['latency_ms'] if it['latency_ms'] is not None else '—'} | "
            f"{it['fallback'] or '—'} | {status} |")
    lines += [
        "",
        "## 方法",
        "",
        "1. 脚本按数字编号与音频文件配对；只评测 `ask/find/highlight/click`，"
        "transport 行（播放/暂停/继续等）不计 Query，只统计跳过数。",
        "2. ASR 可选：`auto` 走本机 Windows SAPI（PowerShell + `System.Speech`，"
        "字典语法）；缺识别器/空结果/报错/超时逐条降级为 `skipped`，不中断；"
        "`none` 时直接用脚本文字当查询，CER 记 null。",
        "3. 检索只读 `reader/book.json` 的 `pages[].text/scene/spots[].name/desc`，"
        "中文 2-gram / 英文单词做 IDF 覆盖打分；命中的 spot 名至少 0.6。"
        "返回 top1 页、连续分和前 3 候选。",
        "4. 问答调用 `qa.answer`（评测期间屏蔽 LLM 密钥，走离线的 rule 路径），"
        "记录 source / fallback / latency_ms；分位数按 qa 自报耗时计算，"
        "另给出包含 ASR 的 wall 分位数。",
        "5. 无人工标注时 `accuracy: null`；只有 `--labels` 提供 `{n: page_id}` 时，"
        "才按 top1 页计算命中率。",
        "",
        "## 局限",
        "",
        "- 检索是词面匹配：同义改写、指代（“它”“那个人”）和跨页推理不在覆盖范围内，"
        "分数低不等于系统一定答错。",
        "- 脚本中的 `find/highlight` 是“导航意图”，真人可能指任意合理页；"
        "本报告只报 top1 连续分与 no_match，不把它等同于正确率。",
        "- SAPI 是听写级通用识别器，没有绘本领域语言模型；同音字（如“高亮”→“高粱”）"
        "会抬高 CER，CER 只反映 ASR 下限，不代表产品链路能力。",
        "- 音频编号缺失（官方素材实收 223 个 wav）记 `no_audio` 跳过，"
        "不补造、不计入覆盖率分子。",
        "- 延迟是单机、单进程、无并发下的结果；LLM 路径未参与本次评测。",
        "",
        f"结果：{m['total']} 条，成功 {m['success']}，跳过 {m['skipped']}",
        "",
    ]
    return "\n".join(lines)


def write_report(report: dict, out: str | Path | None = None) -> dict[str, str]:
    base = Path(out) if out else RUNS / report["book"] / "eval"
    try:
        base.mkdir(parents=True, exist_ok=True)
        json_path = base / "query_report.json"
        md_path = base / "query_report.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        md_path.write_text(render_markdown(report), encoding="utf-8")
    except OSError as exc:
        raise EvaluateError(f"报告写入失败：{base}（{exc}）") from None
    return {"json": str(json_path), "md": str(md_path)}
