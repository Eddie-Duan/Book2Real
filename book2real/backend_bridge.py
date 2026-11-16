"""backend 入库结果 → 阅读器包（``books/<id>/reader``）的桥。

融合后绘本入库只有一条权威路径：backend 的 ``app.book.ingest`` 把 PDF 文字层 /
RapidOCR / 脚本解析的结果写进 ``data/books/<id>/book.json``。本模块把它翻译成
:func:`book2real.storybook.build` 需要的 OCR block（像素框）与正文，避免再跑
一遍 OCR，也避免出现“阅读器和问答各有一份文字”的两个标准。

找不到 backend 数据时返回 ``None``，调用方继续走原来的本地 OCR（离线夹具、
自造书都不受影响）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class BackendBook:
    """``data/books/<id>/book.json`` 的只读视图。"""

    def __init__(self, book_id: str, data: dict) -> None:
        self.id = book_id
        self.data = data or {}
        self.pages: dict[int, dict] = {}
        for page in self.data.get("pages") or []:
            try:
                self.pages[int(page.get("no"))] = page
            except (TypeError, ValueError):
                continue
        self._elements: dict[int, list[dict]] = {}
        for element in self.data.get("elements") or []:
            try:
                self._elements.setdefault(int(element.get("page") or 0), []).append(element)
            except (TypeError, ValueError):
                continue

    # ------------------------------------------------------------- loading
    @classmethod
    def load(cls, book_dir: str | Path, data_dir: str | Path | None = None) -> Optional["BackendBook"]:
        book_id = Path(book_dir).name
        path = Path(data_dir or DATA_DIR) / "books" / book_id / "book.json"
        if not path.is_file():
            return None
        try:
            return cls(book_id, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    # ------------------------------------------------------------- pages
    def page(self, no: int) -> Optional[dict]:
        return self.pages.get(int(no))

    def story_text(self, no: int) -> str:
        return str((self.page(no) or {}).get("story_text") or "").strip()

    def teaching_text(self, no: int) -> str:
        return str((self.page(no) or {}).get("teaching_text") or "").strip()

    def blocks(self, no: int, width: float, height: float) -> list[dict]:
        """归一化 bbox → 目标页图的像素框（与 storybook.ocr_blocks 同形）。"""
        page = self.page(no) or {}
        out: list[dict] = []
        for block in page.get("blocks") or []:
            text = str(block.get("text") or "").strip()
            box = block.get("bbox") or []
            if not text or len(box) != 4:
                continue
            try:
                x0, y0, x1, y1 = (float(v) for v in box)
            except (TypeError, ValueError):
                continue
            out.append(
                {
                    "x0": x0 * width,
                    "y0": y0 * height,
                    "x1": x1 * width,
                    "y1": y1 * height,
                    "text": text,
                    "score": float(block.get("score") or 1.0),
                    "kind": str(block.get("kind") or "story"),
                }
            )
        return out

    def story_boxes(self, no: int, width: float, height: float) -> list[dict]:
        return [b for b in self.blocks(no, width, height) if b.get("kind") == "story"]

    def spots(self, no: int) -> list[dict]:
        """backend 的触控元素卡 → 阅读器 spot（归一化 xywh + desc）。"""
        out: list[dict] = []
        for element in self._elements.get(int(no), []):
            box = element.get("bbox")
            if not (isinstance(box, list) and len(box) == 4):
                continue
            try:
                x0, y0, x1, y1 = (float(v) for v in box)
            except (TypeError, ValueError):
                continue
            out.append(
                {
                    "name": str(element.get("name") or "").strip(),
                    "box": [round(x0, 4), round(y0, 4), round(max(0.0, x1 - x0), 4), round(max(0.0, y1 - y0), 4)],
                    "desc": str(element.get("info") or "").strip(),
                }
            )
        return [s for s in out if s["name"]]

    def script_items(self) -> list[dict]:
        return list((self.data.get("script") or {}).get("items") or [])

    def elements(self) -> list[dict]:
        return list(self.data.get("elements") or [])
