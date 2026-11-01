"""Import photographed / downloaded book pages into `books/<id>/`.

The usual source is a folder of files the author copied out of a gallery or
chat app in reading order. Windows keeps the copy time in `st_ctime`, so
sorting by it recovers that order; `st_mtime` is the fallback on filesystems
that do not expose creation time.

    python scripts/import_book.py --src assets/book2 --book charlie_lola \
        --title "我特别了解豚鼠" --subtitle "查理与劳拉"

Writes `books/<id>/pages/pNN.<ext>`, `book.json` (if missing) and
`ORDER.csv`, an audit trail from page number back to the source file.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MOVES = ["dolly_in", "kenburns", "pan_lr", "dolly_out", "pan_rl", "dolly_in", "kenburns"]


def creation_time(p: Path) -> float:
    st = p.stat()
    return st.st_ctime if os.name == "nt" else st.st_mtime


def collect(src: Path) -> list[Path]:
    if src.is_file():
        return [src]
    files = [p for p in src.iterdir() if p.suffix.lower() in IMAGE_EXT]
    return sorted(files, key=lambda p: (creation_time(p), p.name.lower()))


def build(src: Path, book_id: str, title: str, subtitle: str, *,
          force: bool = False, fps: int = 24) -> Path:
    book_dir = ROOT / "books" / book_id
    pages_dir = book_dir / "pages"
    files = collect(src)
    if not files:
        raise SystemExit(f"no images found under {src}")
    if pages_dir.exists() and any(pages_dir.iterdir()) and not force:
        raise SystemExit(f"{pages_dir} is not empty; pass --force to overwrite")
    pages_dir.mkdir(parents=True, exist_ok=True)

    rows, pages = [], []
    for i, f in enumerate(files, start=1):
        ext = f.suffix.lower()
        name = f"p{i:02d}{ext}"
        shutil.copy2(f, pages_dir / name)
        with Image.open(f) as im:
            w, h = im.size
        aspect = w / h
        mode = "fill" if aspect >= 1.2 else "card"
        pages.append({
            "file": name,
            "text": "",
            "move": MOVES[(i - 1) % len(MOVES)],
            "mode": mode,
            "duration": 3.5 if i == 1 else 5.0,
        })
        rows.append({
            "page": i, "file": name, "source": f.name,
            "created": datetime.fromtimestamp(creation_time(f)).isoformat(timespec="seconds"),
            "bytes": f.stat().st_size, "width": w, "height": h,
        })

    book = {"book_id": book_id, "title": title, "subtitle": subtitle,
            "fps": fps, "pages": pages}
    meta = book_dir / "book.json"
    if meta.exists() and not force:
        print(f"kept existing {meta}", file=sys.stderr)
    else:
        meta.write_text(json.dumps(book, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    with open(book_dir / "ORDER.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"{len(files)} pages -> {pages_dir}")
    for r in rows:
        print(f"  p{r['page']:02d}  <- {r['created']}  {r['width']}x{r['height']}  {r['source']}")
    return book_dir


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True)
    ap.add_argument("--book", required=True, help="book id, also the folder name")
    ap.add_argument("--title", required=True)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    build(Path(a.src), a.book, a.title, a.subtitle, force=a.force, fps=a.fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
