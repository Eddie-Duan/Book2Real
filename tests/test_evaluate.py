"""Self-contained tests for book2real.evaluate — stdlib only, no network, no real audio.

    python tests/test_evaluate.py

Last line prints `N tests passed`.  A fake book package (book.json + reader
book.json), a fake 50-line-style script and RIFF-header-only wav files are
built in a temp directory, so these tests exercise the report contract,
transport skipping, --limit, CER/accuracy honesty, ASR degradation and the
clear-error path without touching the official samples or the runs/ tree.
"""
from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from book2real import cli, evaluate  # noqa: E402

REQUIRED_ITEM_KEYS = {
    "n", "script", "kind", "audio", "transcript", "cer", "top_page", "score",
    "candidates", "no_match", "answer", "latency_ms", "source", "fallback",
    "status", "reason",
}
ALLOWED_FALLBACKS = {None, "no_llm_key", "llm_error", "no_evidence", "qa_unavailable"}

#: 2 transport rows, 4 evaluable rows (find / ask / highlight / ask), one of
#: which (05) deliberately has no audio.
SCRIPT = """\
01. 播放 （必选）
02. 找到小猫玩球的画面
    [点击] 点击小猫 | 展示小猫 （必选）
03. 小猫在哪里？
04. 高亮“小猫”
05. 小狗追蝴蝶
06. 继续
"""

PAGES = [
    {"id": "p01", "text": "小猫在草地上玩球", "scene": "晴天，小猫追着红球跑",
     "spots": [{"name": "小猫", "box": [0.1, 0.1, 0.2, 0.2],
                "desc": "一只橘色小猫在草地上扑球"}]},
    {"id": "p02", "text": "小狗追着蝴蝶跑", "scene": "花园里，小狗追蝴蝶",
     "spots": [{"name": "小狗", "box": [0.5, 0.5, 0.2, 0.2],
                "desc": "一只棕色小狗追蝴蝶"}]},
    {"id": "p03", "text": "下雨了，大家回家", "scene": "雨天的小路",
     "spots": []},
]


def _fake_wav(path: Path) -> None:
    data = b"\x00\x00" * 800                     # 0.05 s of silence @ 16 kHz
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    header = (b"RIFF" + struct.pack("<I", 4 + len(fmt) + 8 + len(data)) + b"WAVE"
              + fmt + b"data" + struct.pack("<I", len(data)))
    path.write_bytes(header + data)


def _fixture(root: Path) -> tuple[Path, Path, Path]:
    """Build books/demo, audio/, script.txt inside ``root``; return their paths."""
    book = root / "books" / "demo"
    (book / "reader").mkdir(parents=True)
    (book / "book.json").write_text(json.dumps({
        "book_id": "demo", "title": "测试书", "language": "zh",
        "pages": [{"file": "p01.jpg"}, {"file": "p02.jpg"}, {"file": "p03.jpg"}],
    }, ensure_ascii=False), encoding="utf-8")
    (book / "reader" / "book.json").write_text(json.dumps({
        "book_id": "demo", "title": "测试书", "language": "zh", "pages": PAGES,
    }, ensure_ascii=False), encoding="utf-8")
    script = root / "script.txt"
    script.write_text(SCRIPT, encoding="utf-8")
    audio = root / "audio"
    audio.mkdir()
    for n in (2, 3, 4):                           # 05 has no audio on purpose
        _fake_wav(audio / f"{n:02d}.wav")
    return book, audio, script


def _run(root: Path, book: Path, audio: Path, script: Path, **kwargs) -> dict:
    return evaluate.evaluate(book=str(book), audio=str(audio),
                             script=str(script), **kwargs)


def _with_fixture():
    tmp = tempfile.TemporaryDirectory(prefix="b2r_eval_")
    root = Path(tmp.name)
    book, audio, script = _fixture(root)
    return tmp, root, book, audio, script


def test_report_contract() -> None:
    tmp, root, book, audio, script = _with_fixture()
    try:
        report = _run(root, book, audio, script, asr="none")

        assert report["book"] == "demo"
        assert report["accuracy"] is None, "accuracy must stay null without labels"
        assert report["labels"] == {"provided": False}
        assert report["asr"]["mode"] == "none"
        assert report["asr"]["available"] is None

        m = report["metrics"]
        assert m["total"] == 4, m
        assert m["success"] == 3, m
        assert m["skipped"] == 1, m
        assert m["transport_skipped"] == 2, "01/06 transport rows must be counted as skips"
        assert m["coverage"] == 0.75, m
        assert m["accuracy"] is None
        assert m["asr"]["attempted"] == 0
        assert m["asr"]["recognized"] == 0
        assert m["retrieval"]["top1_mean_score"] > 0

        items = report["items"]
        assert [it["n"] for it in items] == [2, 3, 4, 5], [it["n"] for it in items]
        assert all(it["kind"] != "transport" for it in items), "transport must not be a query"
        by_n = {it["n"]: it for it in items}
        assert by_n[5]["status"] == "skipped" and by_n[5]["reason"] == "no_audio"
        for n in (2, 3, 4):
            it = by_n[n]
            assert REQUIRED_ITEM_KEYS <= set(it), REQUIRED_ITEM_KEYS - set(it)
            assert it["status"] == "ok", it
            assert it["transcript"] is None and it["cer"] is None
            assert it["top_page"] in {"p01", "p02", "p03"}
            assert isinstance(it["score"], float) and it["score"] > 0
            assert it["answer"].strip(), "QA must return a non-empty answer"
            assert isinstance(it["latency_ms"], int) and it["latency_ms"] >= 0
            assert it["fallback"] in ALLOWED_FALLBACKS
            assert it["source"] in ("rule", "llm")
            assert isinstance(it["candidates"], list) and it["candidates"]
        assert by_n[2]["top_page"] == "p01"
        assert by_n[3]["top_page"] == "p01"
        assert by_n[4]["top_page"] == "p01"
        assert by_n[5]["top_page"] is None

        paths = evaluate.write_report(report, out=root / "runs")
        saved = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
        assert saved["metrics"]["transport_skipped"] == 2
        assert saved["accuracy"] is None
        md = Path(paths["md"]).read_text(encoding="utf-8")
        assert "accuracy：null" in md
        assert "## 方法" in md and "## 局限" in md
        assert md.rstrip().endswith("结果：4 条，成功 3，跳过 1")
    finally:
        tmp.cleanup()


def test_limit_only_touches_queries() -> None:
    tmp, root, book, audio, script = _with_fixture()
    try:
        report = _run(root, book, audio, script, asr="none", limit=2)
        assert [it["n"] for it in report["items"]] == [2, 3]
        assert report["metrics"]["total"] == 2
        assert report["metrics"]["success"] == 2
        assert report["metrics"]["transport_skipped"] == 2
    finally:
        tmp.cleanup()


def test_labels_drive_accuracy() -> None:
    tmp, root, book, audio, script = _with_fixture()
    try:
        good = {"2": "p01", "3": "p01", "4": "p01", "99": "p01"}
        labels = root / "labels_good.json"
        labels.write_text(json.dumps(good), encoding="utf-8")
        report = _run(root, book, audio, script, asr="none", labels=str(labels))
        assert report["accuracy"] == 1.0, report["accuracy"]
        assert report["labels"]["labeled_items"] == 3, "unknown n=99 must not join the denominator"
        assert report["labels"]["hits"] == 3

        labels.write_text(json.dumps({"2": "p02"}), encoding="utf-8")
        report = _run(root, book, audio, script, asr="none", labels=str(labels))
        assert report["accuracy"] == 0.0
        assert report["labels"]["labeled_items"] == 1
        assert report["metrics"]["accuracy"] == 0.0
    finally:
        tmp.cleanup()


def test_cer_and_percentile() -> None:
    assert evaluate.cer("高亮积木城堡", "高亮积木城堡", "zh") == 0.0
    noisy = evaluate.cer("高亮积木城堡", "高粱进入重蹈", "zh")
    assert noisy is not None and 0.0 < noisy <= 1.0, noisy
    assert evaluate.cer("which film", "which films", "en") == 0.5
    assert evaluate.cer("", "任意", "zh") is None
    assert evaluate.cer("Which film?", "which film", "en") == 0.0
    assert evaluate._percentile([], 50) is None
    assert evaluate._percentile([10], 95) == 10
    assert evaluate._percentile([1, 2, 3, 4], 50) == 2.5
    assert evaluate._percentile([1, 2, 3, 4], 95) == 3.85


def test_asr_failure_degrades_to_skipped() -> None:
    tmp, root, book, audio, script = _with_fixture()
    old_probe, old_rec = evaluate.recognizer_available, evaluate.recognize_wav
    try:
        evaluate.recognizer_available = lambda culture: True
        evaluate.recognize_wav = lambda path, culture, timeout: ("", "asr_timeout")
        report = _run(root, book, audio, script, asr="auto")
        assert report["metrics"]["success"] == 0
        assert report["metrics"]["skipped"] == 4
        reasons = {it["n"]: it["reason"] for it in report["items"]}
        assert reasons[5] == "no_audio"
        assert [reasons[n] for n in (2, 3, 4)] == ["asr_timeout"] * 3
        assert report["accuracy"] is None

        evaluate.recognizer_available = lambda culture: False
        report = _run(root, book, audio, script, asr="auto")
        reasons = {it["n"]: it["reason"] for it in report["items"]}
        assert [reasons[n] for n in (2, 3, 4)] == ["asr_unavailable"] * 3
        assert report["metrics"]["asr"]["failed"] == 3
        assert report["metrics"]["coverage"] == 0.0
        assert report["accuracy"] is None
    finally:
        evaluate.recognizer_available, evaluate.recognize_wav = old_probe, old_rec
        tmp.cleanup()


def test_bad_inputs_are_clear() -> None:
    tmp, root, book, audio, script = _with_fixture()
    try:
        cases = [
            (dict(book=str(book), audio=str(root / "nope"), script=str(script)), "音频目录不存在"),
            (dict(book=str(book), audio=str(audio), script=str(root / "nope.txt")), "录制脚本不存在"),
            (dict(book=str(book), audio=str(audio), script=str(script), limit=0), "--limit"),
            (dict(book=str(book), audio=str(audio), script=str(script), asr="bogus"), "--asr"),
        ]
        for kwargs, needle in cases:
            try:
                evaluate.evaluate(**kwargs)
            except evaluate.EvaluateError as exc:
                assert needle in str(exc), (needle, str(exc))
            else:
                raise AssertionError(f"expected EvaluateError for {kwargs}")
        (book / "reader" / "book.json").unlink()
        try:
            evaluate.evaluate(book=str(book), audio=str(audio), script=str(script))
        except evaluate.EvaluateError as exc:
            assert "reader/book.json" in str(exc), exc
        else:
            raise AssertionError("expected EvaluateError for a missing reader package")
    finally:
        tmp.cleanup()


def test_cli_error_has_no_traceback() -> None:
    tmp, root, book, audio, script = _with_fixture()
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["evaluate", "--book", str(book),
                           "--audio", str(root / "missing"), "--script", str(script),
                           "--asr", "none"])
        assert rc == 2, rc
        assert "Traceback" not in err.getvalue(), err.getvalue()
        assert "音频目录不存在" in err.getvalue(), err.getvalue()
    finally:
        tmp.cleanup()


def test_cli_writes_reports_and_final_line() -> None:
    tmp, root, book, audio, script = _with_fixture()
    old_runs = evaluate.RUNS
    evaluate.RUNS = root / "runs"
    out, err = io.StringIO(), io.StringIO()
    try:
        extra = root / "copy.json"
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["evaluate", "--book", str(book), "--audio", str(audio),
                           "--script", str(script), "--asr", "none",
                           "--json", str(extra)])
        assert rc == 0, err.getvalue()
        lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
        assert lines and lines[-1] == "结果：4 条，成功 3，跳过 1", lines[-1]
        report_path = root / "runs" / "demo" / "eval" / "query_report.json"
        md_path = root / "runs" / "demo" / "eval" / "query_report.md"
        assert report_path.is_file() and md_path.is_file()
        assert json.loads(extra.read_text(encoding="utf-8"))["metrics"]["total"] == 4
        assert json.loads(report_path.read_text(encoding="utf-8"))["accuracy"] is None
    finally:
        evaluate.RUNS = old_runs
        tmp.cleanup()


if __name__ == "__main__":
    ok = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print(f"ok  {name}")
    print(f"{ok} tests passed")
