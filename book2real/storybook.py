"""Build an interactive storybook package from scanned page images.

Pipeline (per book):
  page images -> OCR (RapidOCR) -> select the story-text column -> join lines
             -> clean the printed text out of the illustration (flat-fill or
                diffusion inpaint) -> crop -> pinyin tokens -> reader.json

Curated content (`books/<id>/curation.json`) overrides OCR text and adds
child-facing explanations, character cards and hotspots. Without curation the
book still works as a read-along: OCR text + pinyin + cleaned art.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import imgops as I
from .util import ensure_dir, imread, sha256_file, write_json

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
if VENDOR.exists() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

CJK = re.compile(r"[\u3400-\u9fff]")
NO_LINE_START = "，。！？、；：”’）》」』…"
DEFAULT_GLOSSARY = {
    "我": "说话的人自己。", "你": "正在听的人。", "他": "那个男孩子。", "她": "那个女孩子。",
    "的": "表示“属于谁”或“什么样的”。", "了": "表示事情已经发生。", "是": "表示“就是”。",
    "在": "表示正在做，或表示地点。", "有": "表示拥有、存在。", "个": "用来数人或东西。",
    "这": "指离自己近的人或东西。", "那": "指离自己远的人或东西。", "不": "表示否定。",
    "很": "表示程度深。", "都": "表示全部。", "也": "表示同样。", "就": "表示紧接着、马上。",
    "说": "用嘴讲出话来。", "看": "用眼睛观察。", "吃": "把食物放进嘴里。", "喝": "把水送进嘴里。",
    "跑": "快速地走。", "好": "让人满意、喜欢。", "小": "体积不大。", "大": "体积很大。",
    "人": "会说话、会思考的生命。", "家": "住的地方，也指家里人。", "里": "内部。",
    "上": "位置在高处。", "下": "位置在低处。", "来": "朝说话的人靠近。", "去": "离开说话的人。",
    "做": "动手完成一件事。", "想": "在头脑里思考。", "要": "希望得到。", "会": "懂得怎样做。",
    "能": "有力量或本领做到。", "把": "用来强调对某样东西做动作。", "被": "表示承受动作。",
    "给": "把东西交给别人。", "和": "表示一起、以及。", "但": "表示转折。", "因为": "说明原因。",
    "所以": "说明结果。", "还": "表示另外、仍然。", "又": "表示再一次。", "再": "表示重复一次。",
}
_OCR = None


def _ocr_engine():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def ocr_blocks(image_path: str | Path) -> list[dict]:
    """Run OCR once per page and normalise the boxes."""
    res, _ = _ocr_engine()(str(image_path))
    out = []
    for box, text, score in (res or []):
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        out.append({
            "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
            "text": str(text), "score": round(float(score), 3),
        })
    return out


def _merge_intervals(spans: list[tuple[float, float]], gap: float) -> list[list[int]]:
    """Group blocks into columns by horizontal interval chaining."""
    order = sorted(range(len(spans)), key=lambda i: spans[i][0])
    groups: list[list[int]] = []
    cur: list[int] = []
    right = -1e9
    for i in order:
        x0, x1 = spans[i]
        if cur and x0 > right + gap:
            groups.append(cur)
            cur = []
            right = -1e9
        cur.append(i)
        right = max(right, x1)
    if cur:
        groups.append(cur)
    return groups


def story_text(blocks: list[dict], page_w: int) -> tuple[str, list[dict]]:
    """Pick the main text column and read it top-to-bottom.

    Picture-book pages put narration in one column; labels baked into the art
    are short, detached fragments. Choosing the column with the most Chinese
    characters keeps the narration and drops the labels.
    """
    cand = []
    for b in blocks:
        h = b["y1"] - b["y0"]
        if 14 <= h <= 58 and b["score"] >= 0.5 and CJK.search(b["text"]):
            cand.append(b)
    if not cand:
        return "", []
    gap = page_w * 0.025
    groups = _merge_intervals([(b["x0"], b["x1"]) for b in cand], gap)
    best, best_score = [], -1
    for g in groups:
        chars = sum(len(CJK.findall(cand[i]["text"])) for i in g)
        if chars > best_score:
            best, best_score = g, chars
    picked = sorted((cand[i] for i in best), key=lambda b: (round(b["y0"] / 8), b["x0"]))
    lines = [b["text"] for b in picked]
    return "".join(lines), picked


def pinyin_tokens(text: str) -> list[dict]:
    """One token per character, with its Mandarin reading (empty for punctuation)."""
    try:
        from pypinyin import Style, pinyin
    except Exception:
        return [{"ch": c, "py": ""} for c in text]
    py = pinyin(text, style=Style.TONE, heteronym=False, errors=lambda x: list(x))
    out = []
    for ch, reading in zip(text, py):
        r = reading[0] if reading else ""
        if ch in "，。！？、；：（）“”‘’…—《》「」":
            r = ""
        out.append({"ch": ch, "py": r})
    return out


def segments(text: str) -> list[str]:
    """Word groups for line breaking; punctuation stays with the word before it."""
    try:
        import jieba
        words = [w for w in jieba.cut(text, HMM=True) if w.strip()]
    except Exception:
        words = list(text)
    if "".join(words) != text:                       # never lose characters
        words = list(text)
    out: list[str] = []
    pending = ""
    for w in words:
        if all(c in "“‘（《「『" for c in w):
            pending += w
            continue
        if pending:
            w, pending = pending + w, ""
        if out and all(c in "，。！？、；：…”’）》…—" for c in w):
            out[-1] += w
        else:
            out.append(w)
    if pending:
        if out:
            out[-1] += pending
        else:
            out.append(pending)
    return out


def _flat_fill(img: np.ndarray, blocks: list[dict],
               extra: list | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Erase printed text.

    Prefer OpenCV's Telea inpaint when available (it reconstructs flat colour
    panels and ruled paper very cleanly); fall back to median-fill + diffusion.
    `extra` boxes cover glyphs the OCR missed (punctuation, stylised display
    type); they are supplied per page in curation.json.
    """
    out = img.copy()
    h, w = img.shape[:2]
    holes = Image.new("L", (w, h), 0)
    hh = ImageDraw.Draw(holes)
    for b in blocks:
        pad = 7
        hh.rectangle((max(0, int(b["x0"] - pad)), max(0, int(b["y0"] - pad)),
                      min(w, int(b["x1"] + pad)), min(h, int(b["y1"] + pad))), fill=255)
    for box in (extra or []):
        x0, y0, x1, y1 = box
        hh.rectangle((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)), fill=255)
    mask = np.asarray(holes) > 127
    if not mask.any():
        return out, mask
    try:
        import cv2                                    # optional, much cleaner
        bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
        fill = cv2.inpaint(bgr, mask.astype(np.uint8) * 255, 4, cv2.INPAINT_TELEA)
        return cv2.cvtColor(fill, cv2.COLOR_BGR2RGB), mask
    except Exception:
        pass

    for b in blocks:
        pad = 5
        x0, y0 = max(0, int(b["x0"] - pad)), max(0, int(b["y0"] - pad))
        x1, y1 = min(w, int(b["x1"] + pad)), min(h, int(b["y1"] + pad))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        rx0, ry0 = max(0, x0 - 12), max(0, y0 - 12)
        rx1, ry1 = min(w, x1 + 12), min(h, y1 + 12)
        ring = img[ry0:ry1, rx0:rx1].reshape(-1, 3)
        yy, xx = np.mgrid[ry0:ry1, rx0:rx1]
        keep = ~((xx >= x0) & (xx < x1) & (yy >= y0) & (yy < y1))
        sample = ring[keep.ravel()]
        if len(sample) < 20:
            continue
        med = np.median(sample, axis=0)
        std = float(sample.astype(np.float32).std(axis=0).mean())
        if std < 15.0 and (x1 - x0) * (y1 - y0) < 0.18 * w * h:
            out[y0:y1, x0:x1] = med.astype(np.uint8)
    return I.inpaint(out, mask), mask


def clean_art(image_path: Path, blocks: list[dict], *, max_side: int = 1400,
              extra: list | None = None) -> np.ndarray:
    img = imread(image_path)
    cleaned, _ = _flat_fill(img, blocks, extra=extra)
    # trim uniform white margins (scanned pages often carry a paper border)
    g = I.to_gray(cleaned)
    ys, xs = np.where(g < 0.965)
    if len(xs):
        pad = 4
        x0, x1 = max(0, xs.min() - pad), min(cleaned.shape[1], xs.max() + 1 + pad)
        y0, y1 = max(0, ys.min() - pad), min(cleaned.shape[0], ys.max() + 1 + pad)
        cleaned = cleaned[y0:y1, x0:x1]
    h, w = cleaned.shape[:2]
    k = min(1.0, max_side / max(h, w))
    if k < 1.0:
        cleaned = I.resize(cleaned, int(round(w * k)), int(round(h * k)))
    return cleaned


def _art_layers(art: np.ndarray, out_dir: Path, page_id: str) -> dict:
    """Optional 2.5D split: foreground PNG + background plate.

    Only used when the decomposition is confident; otherwise the reader uses
    the flat art with CSS parallax.
    """
    from .layers import decompose
    pl = decompose(art, work=900)
    if pl.count < 2:
        return {}
    near = pl.layers[-1]
    a = np.clip(near.alpha, 0, 1)
    if float((a > 0.5).mean()) < 0.04 or float((a > 0.5).mean()) > 0.8:
        return {}
    fg = np.dstack([near.rgb, (a * 255).astype(np.uint8)])
    fg_p = out_dir / f"{page_id}_fg.png"
    Image.fromarray(fg, "RGBA").save(fg_p)
    far = pl.layers[0]
    bg = np.dstack([far.rgb, (np.clip(far.alpha, 0, 1) * 255).astype(np.uint8)])
    bg_p = out_dir / f"{page_id}_bg.png"
    Image.fromarray(bg, "RGBA").save(bg_p)
    return {"fg": fg_p.name, "bg": bg_p.name, "layers": pl.count}


def _split_text(text: str, limit: int = 96) -> list[str]:
    """Split narration into readable screens without breaking a quotation."""
    if len(text) <= limit * 1.06:
        return [text]
    units = [u for u in re.findall(r"[^。！？；…]*[。！？；…]+[”’]?", text) if u]
    consumed = sum(len(u) for u in units)
    if consumed < len(text):
        units.append(text[consumed:])

    chunks, buf, depth = [], "", 0
    for u in units:
        if buf and len(buf) + len(u) > limit and depth == 0:
            chunks.append(buf)
            buf = ""
        elif buf and len(buf) + len(u) > limit * 1.35:
            chunks.append(buf)
            buf = ""
        buf += u
        depth += u.count("“") - u.count("”")
    if buf:
        chunks.append(buf)
    return chunks or [text]


def load_book(book_dir: Path) -> dict:
    return json.loads((book_dir / "book.json").read_text(encoding="utf-8"))


def build(book_dir: str | Path, *, force: bool = False, max_side: int = 1400,
          on_progress=None) -> dict:
    book_dir = Path(book_dir).resolve()
    meta = load_book(book_dir)
    out_dir = ensure_dir(book_dir / "reader")
    art_dir = ensure_dir(out_dir / "art")
    cur_path = book_dir / "curation.json"
    curation = json.loads(cur_path.read_text(encoding="utf-8")) if cur_path.exists() else {}
    cpages = curation.get("pages", {})
    total = len(meta["pages"])

    pages = []
    for i, pg in enumerate(meta["pages"], start=1):
        if on_progress:
            on_progress(i - 1, total, f"识别第 {i}/{total} 页")
        pid = f"p{i:02d}"
        src = book_dir / "pages" / pg["file"]
        ocr_p = out_dir / "ocr" / f"{pid}.json"
        if ocr_p.exists() and not force:
            blocks = json.loads(ocr_p.read_text(encoding="utf-8"))
        else:
            blocks = ocr_blocks(src)
            write_json(ocr_p, blocks)
        text, picked = story_text(blocks, imread(src).shape[1])
        c = cpages.get(pid, {})
        if c.get("skip"):
            continue
        text = (c.get("text") or text).strip()
        text = re.sub(r"\s+", "", text)
        art_p = art_dir / f"{pid}.jpg"
        if force or not art_p.exists():
            art = clean_art(src, blocks, max_side=max_side, extra=c.get("clean_boxes"))
            box = c.get("crop")
            if box:
                h, w = art.shape[:2]
                x, y, cw, chh = box
                art = art[int(y * h):int((y + chh) * h), int(x * w):int((x + cw) * w)]
            Image.fromarray(art).save(art_p, quality=93)
        else:
            art = imread(art_p)
        chunks = _split_text(text)
        for ci, chunk in enumerate(chunks):
            sid = pid if len(chunks) == 1 else f"{pid}-{ci + 1}"
            pages.append({
                "id": sid,
                "source_page": i,
                "part": ci + 1,
                "parts": len(chunks),
                "kind": c.get("kind", "page"),
                "text": chunk,
                "pinyin": pinyin_tokens(chunk),
                "segments": segments(chunk),
                "art": f"art/{pid}.jpg",
                # 2.5D is done with a camera move on the complete plate: cutting
                # printed art into fg/bg layers leaves halos around linework.
                "art_fg": "",
                "art_bg": "",
                "art_w": int(art.shape[1]), "art_h": int(art.shape[0]),
                "scene": c.get("scene", ""),
                "words": c.get("words", []),
                "spots": c.get("spots", []),
                "characters": c.get("characters", []),
                "ocr_lines": [{"text": b["text"], "score": b["score"], "box": [b["x0"], b["y0"], b["x1"], b["y1"]]} for b in blocks],
                "text_boxes": [[b["x0"], b["y0"], b["x1"], b["y1"]] for b in picked],
            })

    characters = curation.get("characters", [])
    for idx, ch in enumerate(characters):
        crop = ch.get("crop")
        if crop:
            pg = next((p for p in pages
                       if p["id"] == crop.get("page")
                       or p["id"].startswith(str(crop.get("page")) + "-")), None)
            if pg:
                if crop.get("from_original"):
                    page_idx = int(str(crop.get("page"))[1:]) - 1
                    art = imread(book_dir / "pages" / meta["pages"][page_idx]["file"])
                else:
                    art = imread(out_dir / pg["art"])
                h, w = art.shape[:2]
                x, y, cw, chh = crop["box"]
                box = (int(x * w), int(y * h), int((x + cw) * w), int((y + chh) * h))
                out_p = art_dir / f"char_{ch['id']}.jpg"
                Image.fromarray(art[box[1]:box[3], box[0]:box[2]]).save(out_p, quality=92)
                characters[idx] = {**ch, "image": f"art/{out_p.name}"}

    data = {
        "book_id": meta.get("book_id", book_dir.name),
        "title": curation.get("title") or meta.get("title", book_dir.name),
        "subtitle": curation.get("subtitle") or meta.get("subtitle", ""),
        "author": curation.get("author", ""),
        "theme": curation.get("theme", {}),
        "characters": characters,
        "glossary": {**DEFAULT_GLOSSARY, **(curation.get("glossary") or {})},
        "pages": pages,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stats": {"pages": len(pages), "ocr_blocks": sum(len(p["ocr_lines"]) for p in pages)},
    }
    write_json(out_dir / "book.json", data)
    if on_progress:
        on_progress(total, total, "完成")
    return data


def load_reader(book_dir: str | Path) -> dict:
    return json.loads((Path(book_dir) / "reader" / "book.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("book")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    d = build(a.book, force=a.force)
    print(json.dumps(d["stats"], ensure_ascii=False))
    for p in d["pages"]:
        print(p["id"], p["text"][:60])
