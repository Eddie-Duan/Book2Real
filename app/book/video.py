"""视频索引：帧采样 + 帧 OCR + 查字框选 + 场景/元素检索。

视频生成由队友负责；本模块只消费最终 MP4 与可选 manifest。
manifest 示例见 docs/INTEGRATION.md。
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from ..config import Settings
from ..utils import normalize_text
from .ocr import OCREngine, WATERMARK_RE

log = logging.getLogger(__name__)


class VideoIndex:
    def __init__(self, settings: Settings, book_id: str) -> None:
        self.settings = settings
        self.book_id = book_id
        self.dir = settings.books_dir / book_id / "video"
        self.index_path = self.dir / "index.json"
        self.data: dict = {"book_id": book_id, "ready": False, "frames": [], "scenes": [], "video": ""}
        if self.index_path.is_file():
            try:
                self.data = json.loads(self.index_path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("bad video index %s", self.index_path)

    @property
    def ready(self) -> bool:
        return bool(self.data.get("ready"))

    def build(
        self,
        video_path: str | Path,
        *,
        manifest_path: Optional[str | Path] = None,
        book: Optional[dict] = None,
        sample_fps: Optional[float] = None,
        max_frames: Optional[int] = None,
        force: bool = False,
    ) -> dict:
        if self.ready and not force:
            return self.data
        import cv2

        video_path = Path(video_path)
        manifest = {}
        if manifest_path and Path(manifest_path).is_file():
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        self.dir.mkdir(parents=True, exist_ok=True)
        frames_dir = self.dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frame_count / fps if fps else 0.0
        sample_fps = sample_fps or self.settings.video_sample_fps
        max_frames = max_frames or self.settings.video_max_frames
        step = max(1, int(round(fps / max(0.1, sample_fps))))
        frames: list[dict] = []
        engine = OCREngine.instance()
        idx = 0
        t0 = time.time()
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0 and len(frames) < max_frames:
                t = idx / fps
                frame_path = frames_dir / f"{len(frames):05d}.jpg"
                cv2.imwrite(str(frame_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                texts = []
                try:
                    for line in engine.recognize(frame_path):
                        if WATERMARK_RE.search(line.text):
                            continue
                        x0, y0, x1, y1 = line.bbox
                        texts.append(
                            {
                                "text": line.text,
                                "bbox": [round(x0 / width, 4), round(y0 / height, 4), round(x1 / width, 4), round(y1 / height, 4)],
                                "score": round(line.score, 3),
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("frame OCR failed at %.2fs: %s", t, exc)
                frames.append(
                    {
                        "t": round(t, 3),
                        "frame": f"/api/video/{self.book_id}/frames/{frame_path.name}",
                        "texts": texts,
                    }
                )
            idx += 1
        cap.release()

        scenes = manifest.get("scenes") or []
        if not scenes and book:
            page_count = max(1, int(book.get("page_count") or 1))
            for i in range(page_count):
                scenes.append(
                    {
                        "page": i + 1,
                        "start": round(duration * i / page_count, 3),
                        "end": round(duration * (i + 1) / page_count, 3),
                        "elements": [e for e in book.get("elements", []) if int(e.get("page") or 0) == i + 1],
                    }
                )
        self.data = {
            "book_id": self.book_id,
            "ready": True,
            "video": str(video_path),
            "width": width,
            "height": height,
            "fps": fps,
            "duration": round(duration, 3),
            "sample_fps": sample_fps,
            "frames": frames,
            "scenes": scenes,
            "manifest": manifest,
            "built_at": time.time(),
            "build_seconds": round(time.time() - t0, 1),
        }
        self.index_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.data

    def search_text(self, needle: str, limit: int = 20) -> list[dict]:
        q = normalize_text(needle)
        if not q:
            return []
        hits: list[dict] = []
        for frame in self.data.get("frames", []):
            for item in frame.get("texts", []):
                if q in normalize_text(item.get("text", "")):
                    hits.append(
                        {
                            "t": frame["t"],
                            "frame": frame["frame"],
                            "text": item["text"],
                            "bbox": item.get("bbox"),
                            "score": item.get("score", 0),
                        }
                    )
        hits.sort(key=lambda h: (-h["score"], h["t"]))
        return hits[:limit]

    def frame_at(self, t: float) -> Optional[dict]:
        frames = self.data.get("frames", [])
        if not frames:
            return None
        return min(frames, key=lambda f: abs(float(f.get("t", 0)) - float(t)))

    def scene_for_page(self, page: int) -> Optional[dict]:
        for scene in self.data.get("scenes", []):
            if int(scene.get("page") or 0) == int(page):
                return scene
        return None

    def scene_at(self, t: float) -> Optional[dict]:
        for scene in self.data.get("scenes", []):
            if float(scene.get("start", 0)) <= t <= float(scene.get("end", 0)):
                return scene
        return None

    def elements_at(self, t: float, book: Optional[dict] = None) -> list[dict]:
        scene = self.scene_at(t)
        if scene and scene.get("elements"):
            return scene["elements"]
        if scene and book:
            page = int(scene.get("page") or 0)
            return [e for e in book.get("elements", []) if int(e.get("page") or 0) == page]
        return []

    def describe(self, t: float, box: Optional[list[float]] = None, book: Optional[dict] = None) -> dict:
        scene = self.scene_at(t) or {}
        page_no = int(scene.get("page") or 0)
        page = None
        if book:
            page = next((p for p in book.get("pages", []) if int(p.get("no") or 0) == page_no), None)
        elements = self.elements_at(t, book)
        chosen = None
        if box and elements:
            best = -1.0
            for element in elements:
                ebox = element.get("bbox")
                if not ebox:
                    continue
                overlap = _iou(box, ebox)
                if overlap > best:
                    best, chosen = overlap, element
        frame = self.frame_at(t)
        return {
            "t": t,
            "page": page_no or None,
            "scene": scene,
            "frame": frame,
            "elements": elements,
            "element": chosen,
            "text": (page or {}).get("story_text", ""),
            "description": (chosen or {}).get("info") or (page or {}).get("story_text", ""),
        }

    def status(self) -> dict:
        return {
            "book_id": self.book_id,
            "ready": self.ready,
            "duration": self.data.get("duration", 0),
            "frames": len(self.data.get("frames", [])),
            "scenes": len(self.data.get("scenes", [])),
            "index_path": str(self.index_path),
        }


def _iou(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    area = max(1e-6, (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter)
    return inter / area
