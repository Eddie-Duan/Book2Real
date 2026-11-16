"""Self-contained tests for PDF ingest, the recording script, and cloud pricing.

    python tests/test_ingest_cloud.py

No network, no API key. The only optional dependency is pymupdf, which the
ingest test needs to *create* a two-page PDF; if it is missing that one test is
reported as skipped (and does not print a false pass).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from book2real import cloud  # noqa: E402
from book2real.ingest import import_pdf, read_script  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "纸书智伴企业预选赛绘本素材" / "绘本素材"


def _tiny_pdf(path: Path, pages: int = 2) -> bool:
    try:
        import pymupdf
    except Exception:
        return False
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 80), f"Hello story page {i + 1}.", fontsize=18)
    doc.save(str(path))
    doc.close()
    return True


def test_import_pdf_renders_pages_and_metadata():
    tmp = Path(tempfile.mkdtemp(prefix="b2r_pdf_"))
    try:
        src = tmp / "story.pdf"
        if not _tiny_pdf(src, 2):
            print("skip  pymupdf not installed")
            return
        rep = import_pdf(src, book_id="demo", out_root=tmp / "books")
        assert rep["pages"] == 2 and rep["rendered"] == 2, rep
        book = json.loads((tmp / "books" / "demo" / "book.json").read_text(encoding="utf-8"))
        assert book["book_id"] == "demo" and len(book["pages"]) == 2
        assert book["language"] == "en", book["language"]
        assert len(book["source"]["sha256"]) == 64, book["source"]
        assert (tmp / "books" / "demo" / "pages" / "p01.jpg").stat().st_size > 500
        assert (tmp / "books" / "demo" / "curation.json").exists()
        # the text layer survives when it is real text
        assert "Hello story page 1" in (tmp / "books" / "demo" / "text" / "p01.txt").read_text(encoding="utf-8")
        again = import_pdf(src, book_id="demo", out_root=tmp / "books")
        assert again["rendered"] == 0 and again["skipped"] == 2, again
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_read_script_parses_official_sample():
    path = SAMPLE / "中文绘本01.txt"
    if not path.exists():
        print("skip  official sample folder not present")
        return
    rows = read_script(path)
    assert len(rows) == 50, len(rows)
    assert rows[0]["n"] == 1 and rows[-1]["n"] == 50
    kinds = {r["kind"] for r in rows}
    assert {"transport", "find", "ask"} <= kinds, kinds
    clicks = [r["click"] for r in rows if r.get("click")]
    assert "积木城堡" in " ".join(clicks), clicks
    assert any(r["required"] for r in rows)


def _env(tmp: Path, **kw) -> tuple[dict, Path]:
    ledger = tmp / "ledger.jsonl"
    cloud.LEDGER = ledger
    base = {"CLOUD_MAX_SPEND_CNY": "50", "SEEDREAM_CNY_PER_IMAGE": "0.2",
            "SEEDANCE_CNY_PER_SEC": "1.0", "DEEPSEEK_CNY_PER_1K_TOKENS": "0.002"}
    base.update(kw)
    return base, ledger


def test_cloud_dry_run_prices_without_a_key_or_ledger_write():
    tmp = Path(tempfile.mkdtemp(prefix="b2r_cloud_"))
    try:
        env, ledger = _env(tmp)
        a = cloud.deepseek([{"role": "user", "content": "hi"}], env=env, dry_run=True)
        b = cloud.seedream("a dog", tmp / "x.png", env=env, dry_run=True)
        c = cloud.seedance("a castle", tmp / "x.mp4", seconds=5, env=env, dry_run=True)
        assert a["cny"] > 0 and b["cny"] == 0.2 and c["cny"] == 5.0
        assert not ledger.exists(), "dry-run must not charge the ledger"
        assert not (tmp / "x.png").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cloud_budget_guard_stops_before_spending():
    tmp = Path(tempfile.mkdtemp(prefix="b2r_cloud_"))
    try:
        env, _ledger = _env(tmp, CLOUD_MAX_SPEND_CNY="1")
        try:
            cloud.seedance("a castle", tmp / "x.mp4", seconds=5, env=env, dry_run=True)
        except cloud.BudgetError as exc:
            assert "预算" in str(exc)
        else:
            raise AssertionError("budget guard did not fire")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cloud_status_reports_ready_from_env():
    env = {"DEEPSEEK_API_KEY": "sk-test", "ARK_API_KEY": "ark-test"}
    rows = cloud.provider_status(env)
    assert all(r["ready"] for r in rows), rows
    assert {r["provider"] for r in rows} == {"deepseek", "seedream", "seedance"}
    empty = cloud.provider_status({})
    assert not any(r["ready"] for r in empty), empty


if __name__ == "__main__":
    ok = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print(f"ok  {name}")
    print(f"{ok} tests passed")
