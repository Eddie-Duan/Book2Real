# -*- coding: utf-8 -*-
"""6 本绘本入库 CLI。

用法：
  python tools/ingest_competition.py --zip "<素材zip路径>/纸书智伴企业预选赛绘本素材.zip" [--force]
  python tools/ingest_competition.py --pdf "<PDF路径>/cn01.pdf" --book-id cn01 --lang zh --script "data/source/scripts/中文绘本01.txt"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.book.ingest import ingest_materials_zip, ingest_pdf  # noqa: E402
from app.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", default="")
    parser.add_argument("--pdf", default="")
    parser.add_argument("--book-id", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--lang", default="")
    parser.add_argument("--script", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    t0 = time.time()
    if args.zip:
        books = ingest_materials_zip(args.zip, settings, force=args.force)
    elif args.pdf:
        book = ingest_pdf(
            args.pdf,
            book_id=args.book_id or None,
            title=args.title or None,
            lang=args.lang or None,
            script_path=args.script or None,
            settings=settings,
            force=args.force,
        )
        books = [book]
    else:
        parser.error("--zip or --pdf is required")
    for book in books:
        print(
            json.dumps(
                {
                    "id": book["id"],
                    "title": book["title"],
                    "lang": book["lang"],
                    "pages": book["page_count"],
                    "summary": book.get("summary", {}),
                },
                ensure_ascii=False,
            )
        )
    print(f"done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
