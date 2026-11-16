"""Import an official picture-book PDF into a `books/<id>` working directory.

The competition gives us picture books as PDFs (one PDF = one book, every page
image inside). The rest of the pipeline speaks one language: a folder with
``pages/p01.jpg``, a ``book.json`` page list and an optional ``curation.json``.
This module is the only place that knows about PDFs.

Design rules:

* the PDF is a *source*, not an artefact we commit - only the rendered page
  images and the book metadata go into ``books/<id>``;
* a page render is deterministic (same PDF + dpi + quality = same JPEG), so a
  re-import is cheap to compare and safe to diff;
* if the PDF has a real text layer we keep it (``text/pNN.txt``); scanned books
  get their text from the OCR step in :mod:`book2real.storybook`, so an empty
  text file is not an error;
* the import never overwrites a hand-edited ``curation.json`` unless forced.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .util import ensure_dir, sha256_file, write_json

MOVES = ("dolly_in", "kenburns", "pan_lr", "dolly_out", "pan_rl")
DEFAULT_DPI = 170
DEFAULT_QUALITY = 92


def _slug(name: str) -> str:
    s = re.sub(r"\.pdf$", "", Path(name).name, flags=re.I)
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", s).strip("-").lower()
    return s or "book"


def _page_stem(i: int) -> str:
    return f"p{i:02d}"


def _text_layer(page) -> str:
    """Return the page's text layer only when it is real text, not a CID mess."""
    try:
        raw = page.get_text("text") or ""
    except Exception:
        return ""
    raw = raw.strip()
    if len(raw) < 2:
        return ""
    # A scanned page gives either nothing or replacement glyphs from a broken
    # CID map. Count real letters; require a couple of them per 40 characters.
    real = sum(1 for c in raw if c.isalpha() or ("\u4e00" <= c <= "\u9fff"))
    if real < max(2, len(raw) // 20):
        return ""
    return raw


def import_pdf(pdf: str | Path, *, book_id: str | None = None, out_root: str | Path = "books",
               dpi: int = DEFAULT_DPI, quality: int = DEFAULT_QUALITY,
               force: bool = False) -> dict:
    """Render every page of ``pdf`` into ``<out_root>/<book_id>``.

    Returns a small report: ``book_id``, ``dir``, ``pages``, ``title``,
    ``text_pages``, ``skipped`` (pages already rendered) and ``source``.
    """
    try:
        import pymupdf
    except Exception as exc:                      # pragma: no cover - env guard
        raise RuntimeError("pymupdf is required to import PDFs: pip install pymupdf") from exc

    pdf = Path(pdf).resolve()
    if not pdf.exists():
        raise FileNotFoundError(pdf)
    bid = book_id or _slug(pdf.name)
    book_dir = Path(out_root) / bid
    pages_dir = ensure_dir(book_dir / "pages")
    text_dir = ensure_dir(book_dir / "text")

    doc = pymupdf.open(pdf)
    if not len(doc):
        raise ValueError(f"PDF has no pages: {pdf}")
    meta_title = (doc.metadata or {}).get("title") or ""
    title = re.sub(r"\.(indd|pdf)$", "", meta_title.strip(), flags=re.I).strip()
    if not title:
        title = re.sub(r"\.pdf$", "", pdf.name, flags=re.I)
    hint = pdf.name + " " + title
    lang = "en" if "英文" in hint else ("zh" if "中文" in hint else ("zh" if _has_cjk(title) else "en"))

    rendered, skipped, text_pages = 0, 0, 0
    page_entries = []
    for i, page in enumerate(doc, start=1):
        stem = _page_stem(i)
        out = pages_dir / f"{stem}.jpg"
        text = _text_layer(page)
        if text:
            text_pages += 1
            (text_dir / f"{stem}.txt").write_text(text, encoding="utf-8")
        else:
            (text_dir / f"{stem}.txt").unlink(missing_ok=True)
        if out.exists() and not force:
            skipped += 1
        else:
            # keep the whole page: the art must not lose its margins, because
            # the film camera pans inside these margins.
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            pix.save(str(out), jpg_quality=quality)
            rendered += 1
        page_entries.append({
            "file": out.name,
            "move": MOVES[(i - 1) % len(MOVES)],
            "mode": "card" if i == 1 else "fill",
            "duration": 6.0,
        })

    book_json = {
        "book_id": bid,
        "title": title,
        "subtitle": "",
        "author": "",
        "fps": 24,
        "layers": False,
        "language": lang,
        "source": {
            "kind": "pdf",
            "name": pdf.name,
            "sha256": sha256_file(pdf),
            "pages": len(doc),
            "dpi": dpi,
        },
        "pages": page_entries,
    }
    bj = book_dir / "book.json"
    if force or not bj.exists():
        write_json(bj, book_json)

    cur = book_dir / "curation.json"
    if force or not cur.exists():
        write_json(cur, {"title": title, "subtitle": "", "author": "", "pages": {}})

    doc.close()
    return {
        "book_id": bid,
        "dir": str(book_dir),
        "pages": len(page_entries),
        "title": title,
        "rendered": rendered,
        "skipped": skipped,
        "text_pages": text_pages,
        "source": str(pdf),
    }


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def read_script(path: str | Path) -> list[dict]:
    """Parse an official recording script (``NN. ...`` lines + ``[点击]`` notes).

    The competition ships a text file per book describing the 50 actions used
    to record the interactive demo. We keep it as data, not as UI copy:
    ``{"n": 4, "text": "找到...", "click": "积木城堡", "required": True}``.
    """
    rows: list[dict] = []
    cur: dict | None = None
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)[.、]\s*(.+)$", line)
        if m:
            cur = {"n": int(m.group(1)), "text": m.group(2).strip(),
                   "click": "", "required": "（必选）" in line}
            cur["text"] = cur["text"].replace("（必选）", "").strip()
            rows.append(cur)
            continue
        m = re.match(r"^\[点击\]\s*([^|｜]+)[|｜]\s*(.*)$", line)
        if m and cur is not None:
            cur["click"] = m.group(1).strip()
            cur["after"] = m.group(2).replace("（必选）", "").strip()
            continue
    for r in rows:
        if "高亮" in r["text"]:
            r["kind"] = "highlight"
        elif r["text"].startswith(("找到", "查找")):
            r["kind"] = "find"
        elif r["click"]:
            r["kind"] = "click"
        elif r["text"].lower() in {"play", "pause", "resume", "继续", "播放", "暂停", "结束播放"}:
            r["kind"] = "transport"
        else:
            r["kind"] = "ask"
    return rows
