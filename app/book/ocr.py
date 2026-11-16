"""PDF 页面文字提取：原生文字层优先，扫描页回落 RapidOCR，过滤水印/教学栏。"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import Settings

log = logging.getLogger(__name__)

WATERMARK_RE = re.compile(
    r"仅供|外传|勿外|纸书智伴|纸书智|北京市|大学生|人工智能|创新大赛|第二届|赛题|参赛使用|"
    r"学而思阅读树|学而思大阅读|学而思内容中心",
    re.I,
)
TEACH_RE = re.compile(r"阅读指导|汉字源流|甲骨文|楷书|金文|小篆|隶书|读一读|说一说|想一想|猜一猜")
PAGE_NO_RE = re.compile(r"^\s*[-—–]?\s*\d{1,4}\s*[-—–]?\s*$")
SENT_END = "。！？!?；;"


@dataclass
class TextLine:
    text: str
    bbox: tuple[float, float, float, float]
    size: float = 0.0
    score: float = 1.0
    kind: str = "story"


class OCREngine:
    _instance: "OCREngine | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._engine = None
        self._init_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "OCREngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _ensure(self):
        if self._engine is None:
            with self._init_lock:
                if self._engine is None:
                    from rapidocr_onnxruntime import RapidOCR

                    self._engine = RapidOCR()
        return self._engine

    def recognize(self, image_path: str | Path) -> list[TextLine]:
        engine = self._ensure()
        result, _ = engine(str(image_path))
        lines: list[TextLine] = []
        for row in result or []:
            box, text, score = row
            text = str(text or "").strip()
            if not text or float(score) < 0.30:
                continue
            if WATERMARK_RE.search(text) or PAGE_NO_RE.match(text):
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(TextLine(text, (min(xs), min(ys), max(xs), max(ys)), 0.0, float(score)))
        return lines


def _native_lines(page) -> list[TextLine]:
    lines: list[TextLine] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text or WATERMARK_RE.search(text) or PAGE_NO_RE.match(text):
                continue
            size = max((float(s.get("size") or 0) for s in spans), default=0.0)
            x0, y0, x1, y1 = line.get("bbox", (0, 0, 0, 0))
            lines.append(TextLine(text, (x0, y0, x1, y1), size))
    return lines


def _join_lines(lines: list[TextLine], lang: str, width: float) -> str:
    if not lines:
        return ""
    # 跨页大图（如 1304x652 的对开页）按左右半页分别排序，先左后右
    halves: list[list[TextLine]] = [[], []] if width > 900 else [lines]
    if width > 900:
        for line in lines:
            halves[0 if (line.bbox[0] + line.bbox[2]) / 2 < width / 2 else 1].append(line)
    pieces: list[str] = []
    for group in halves:
        group = sorted(group, key=lambda l: (round(l.bbox[1] / max(6, (l.size or 12) * 0.6)), l.bbox[0]))
        sep = "" if lang == "zh" else " "
        for line in group:
            text = line.text.strip()
            if not text:
                continue
            if pieces and pieces[-1] and (pieces[-1][-1] in "，。！？；,.!?;" or text[0] in "，。！？；,.!?;"):
                sep = ""
            pieces.append(sep + text if pieces else text)
    return re.sub(r"\s+", " ", "".join(pieces)).strip()


def _classify(lines: list[TextLine], page_height: float, ocr: bool = False) -> tuple[list[TextLine], list[TextLine]]:
    story: list[TextLine] = []
    teaching: list[TextLine] = []
    if ocr:
        for line in lines:
            line.kind = "story"
        return lines, teaching
    for line in lines:
        y = (line.bbox[1] + line.bbox[3]) / 2
        if TEACH_RE.search(line.text) or y > page_height * 0.90:
            line.kind = "teaching"
            teaching.append(line)
        elif line.size and line.size < 14 and y > page_height * 0.82:
            line.kind = "teaching"
            teaching.append(line)
        else:
            line.kind = "story"
            story.append(line)
    return story, teaching


def extract_page(page, dpi: int, image_path: Path, lang: str, settings: Settings) -> dict:
    page_width, page_height = float(page.rect.width), float(page.rect.height)
    lines = _native_lines(page)
    story, teaching = _classify(lines, page_height)
    story_text = _join_lines(story, lang, page_width)
    ocr_used = False
    norm_width, norm_height = page_width, page_height

    if len(story_text.strip()) < 2 and settings.ocr_use_rapidocr:
        try:
            ocr_lines = OCREngine.instance().recognize(image_path)
            if ocr_lines:
                norm_width = page_width * dpi / 72.0
                norm_height = page_height * dpi / 72.0
                lines = ocr_lines
                story, teaching = _classify(lines, norm_height, ocr=True)
                story_text = _join_lines(story, lang, norm_width)
                ocr_used = True
        except Exception as exc:  # noqa: BLE001
            log.warning("OCR failed for %s: %s", image_path, exc)

    # 若 OCR 后仍然把整页都归到 story，不做二次修正；教学栏只用于检索补充
    teaching_text = _join_lines(teaching, lang, norm_width)
    blocks = []
    for line in lines:
        x0, y0, x1, y1 = line.bbox
        blocks.append(
            {
                "text": line.text.strip(),
                "bbox": [
                    round(max(0.0, min(1.0, x0 / norm_width)), 4),
                    round(max(0.0, min(1.0, y0 / norm_height)), 4),
                    round(max(0.0, min(1.0, x1 / norm_width)), 4),
                    round(max(0.0, min(1.0, y1 / norm_height)), 4),
                ],
                "size": round(line.size, 1),
                "score": round(line.score, 3),
                "kind": line.kind,
            }
        )
    return {
        "story_text": story_text,
        "teaching_text": teaching_text,
        "all_text": (story_text + " " + teaching_text).strip(),
        "blocks": blocks,
        "ocr": ocr_used,
        "page_width_pt": page_width,
        "page_height_pt": page_height,
    }


def detect_watermark_only(text: str) -> bool:
    stripped = WATERMARK_RE.sub("", text or "").strip()
    return len(stripped) < 8


def looks_scanned(text: str, min_chars: int = 40) -> bool:
    return len(WATERMARK_RE.sub("", text or "").strip()) < min_chars
