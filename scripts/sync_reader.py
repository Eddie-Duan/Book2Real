"""用 backend 的入库结果重建 ``books/<id>/reader``（融合后阅读器包的唯一来源）。

    py scripts/sync_reader.py                 # 全部有 backend 数据的书
    py scripts/sync_reader.py --book cn01     # 只重建一本
    py scripts/sync_reader.py --force         # 重跑图片清洗（慢）

前置：``py backend/tools/ingest_competition.py --path <素材目录>`` 已把
``data/books/<id>/book.json`` 写好。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from book2real import storybook  # noqa: E402
from book2real.backend_bridge import BackendBook  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--books", default=str(ROOT / "books"))
    parser.add_argument("--book", action="append", default=[], help="只重建这些 book id")
    parser.add_argument("--force", action="store_true", help="重新清洗页图")
    args = parser.parse_args()

    root = Path(args.books)
    wanted = set(args.book) if args.book else None
    rebuilt = skipped = 0
    for book_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if wanted and book_dir.name not in wanted:
            continue
        if BackendBook.load(book_dir) is None:
            print(f"skip {book_dir.name}: 没有 data/books/{book_dir.name}/book.json")
            skipped += 1
            continue
        t0 = time.time()
        data = storybook.build(book_dir, force=args.force)
        pages = len(data.get("pages") or [])
        print(f"ok   {book_dir.name}: {pages} pages, {time.time() - t0:.1f}s")
        rebuilt += 1
    print(f"rebuilt {rebuilt}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
