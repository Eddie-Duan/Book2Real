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


SPLIT_ART = re.compile(r"^p\d+-\d+\.jpg$")
TEXT_LIMIT = 64          # characters per reader screen (curation.json: "text_limit")
MAX_PARTS = 4            # screens per page; more than this gets repetitive
ZOOMS = (1.0, 1.3, 1.55, 1.75)   # framing per screen: whole page, then closer views


SENTENCE = re.compile(r"[^。！？；…]*[。！？；…]+[”’]?")
CLAUSE = re.compile(r"[^，、：]*[，、：]?")


def _sentence_units(text: str) -> list[str]:
    units = [u for u in SENTENCE.findall(text) if u]
    if "".join(units) != text:                       # never lose characters
        units = [u for u in CLAUSE.findall(text) if u] or [text]
    return units


def _split_long_unit(unit: str, limit: int) -> list[str]:
    """Break a long sentence at its own commas so no screen carries a wall of text."""
    if len(unit) <= limit * 1.15:
        return [unit]
    pieces = [p for p in CLAUSE.findall(unit) if p]
    if len(pieces) <= 1 or "".join(pieces) != unit:
        return [unit]
    out: list[str] = []
    buf = ""
    for p in pieces:
        if buf and len(buf) + len(p) > limit:
            out.append(buf)
            buf = ""
        buf += p
    if buf:
        out.append(buf)
    return out


def _auto_words(out_dir: Path, pid: str, text: str) -> list[dict]:
    """Words generated by `book2real words` for one page (cached on disk)."""
    import json as _json

    base = Path(out_dir)
    path = base / "words" / f"{pid}.json"
    if not path.exists():
        return []
    try:
        rows = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return []
    return [r for r in rows if r.get("w") and r.get("w") in text]


def _merge_words(curated: list[dict], auto: list[dict], text: str) -> list[dict]:
    """Curated entries first; auto ones only for words not already covered."""
    seen = {w.get("w") for w in curated if w.get("w")}
    out = list(curated)
    for w in auto:
        if w.get("w") in seen:
            continue
        seen.add(w.get("w"))
        out.append(w)
    return [w for w in out if w.get("w") and w["w"] in text]


def _split_text(text: str, limit: int = TEXT_LIMIT, max_parts: int = MAX_PARTS) -> list[str]:
    """Split narration into readable screens at sentence (or comma) boundaries.

    The concatenation of the result is always exactly `text`: nothing is added,
    dropped or re-quoted, so pinyin, segments and the printed book stay in sync.
    """
    if len(text) <= limit * 1.06:
        return [text]
    units = [p for u in _sentence_units(text) for p in _split_long_unit(u, limit)]

    chunks: list[str] = []
    buf = ""
    for u in units:
        if buf and len(buf) + len(u) > limit and len(chunks) < max_parts - 1:
            chunks.append(buf)
            buf = ""
        buf += u
    if buf:
        chunks.append(buf)

    while len(chunks) > max_parts:                   # merge the two shortest neighbours
        i = min(range(len(chunks) - 1), key=lambda k: len(chunks[k]) + len(chunks[k + 1]))
        chunks[i:i + 2] = [chunks[i] + chunks[i + 1]]
    return [c for c in chunks if c] or [text]


def _ink_centre(art: np.ndarray) -> tuple[float, float]:
    """Normalised centroid of the drawn content — where a close-up should look."""
    g = I.to_gray(art)
    ink = g < 0.94
    if not ink.any():
        return 0.5, 0.5
    h, w = g.shape[:2]
    ys, xs = np.where(ink)
    return float(xs.mean()) / w, float(ys.mean()) / h


def _part_boxes(art: np.ndarray, n: int,
                spots: list[dict] | None = None) -> list[tuple[float, float, float, float]]:
    """One framing per reader screen: whole page first, then closer views.

    Crops are never sliced through the middle of a figure. When the page has
    curated hotspots, the later screens frame those characters; otherwise they
    zoom toward the drawn content, which reads as a detail shot.
    """
    if n <= 1:
        return [(0.0, 0.0, 1.0, 1.0)]
    h_px, w_px = art.shape[:2]
    # a low-resolution page must not be zoomed far: the crop would be mush
    reach = min(1.0, max(0.55, min(w_px, h_px) / 700.0))
    targets: list[tuple[float, float, float, float]] = [(*_ink_centre(art), 0.0, 0.0)]
    for s in spots or []:
        box = s.get("box")
        if box and len(box) >= 4:
            targets.append((float(box[0]) + float(box[2]) / 2,
                            float(box[1]) + float(box[3]) / 2,
                            float(box[2]), float(box[3])))
    boxes = [(0.0, 0.0, 1.0, 1.0)]
    for i in range(1, n):
        zoom = 1.0 + (ZOOMS[min(i, len(ZOOMS) - 1)] - 1.0) * reach
        w = h = 1.0 / zoom
        cx, cy, sw, sh = targets[(i - 1) % len(targets)]
        if sw and sh:
            # a character framing must contain its hotspot with a margin
            w, h = max(w, min(1.0, sw * 2.2)), max(h, min(1.0, sh * 2.2))
        x = min(max(cx - w / 2, 0.0), 1.0 - w)
        y = min(max(cy - h / 2, 0.0), 1.0 - h)
        boxes.append((x, y, w, h))
    return boxes


def _remap(items: list[dict], crop: tuple[float, float, float, float]) -> list[dict]:
    """Re-express normalised boxes relative to a crop; drop what is off-frame."""
    cx, cy, cw, ch = crop
    out = []
    for it in items or []:
        box = it.get("box")
        if not box or len(box) < 4:
            out.append(it)
            continue
        x, y, w, h = (float(v) for v in box[:4])
        if w <= 0 or h <= 0:
            out.append(it)
            continue
        ix = max(0.0, min(x + w, cx + cw) - max(x, cx))
        iy = max(0.0, min(y + h, cy + ch) - max(y, cy))
        if ix * iy < 0.35 * w * h:
            continue
        out.append({**it, "box": [(x - cx) / cw, (y - cy) / ch, w / cw, h / ch]})
    return out


def _save_part_art(art: np.ndarray, art_dir: Path, sid: str,
                   crop: tuple[float, float, float, float]) -> tuple[str, int, int]:
    h, w = art.shape[:2]
    cx, cy, cw, ch = crop
    x0, y0 = int(round(cx * w)), int(round(cy * h))
    x1, y1 = int(round((cx + cw) * w)), int(round((cy + ch) * h))
    x1, y1 = min(w, max(x0 + 8, x1)), min(h, max(y0 + 8, y1))
    part = art[y0:y1, x0:x1]
    path = art_dir / f"{sid}.jpg"
    Image.fromarray(part).save(path, quality=93)
    return f"art/{path.name}", int(part.shape[1]), int(part.shape[0])


def load_book(book_dir: Path) -> dict:
    return json.loads((book_dir / "book.json").read_text(encoding="utf-8"))


def _prune_part_art(art_dir: Path, used: set[str]) -> int:
    """Drop crops left over from an earlier split (a page may need fewer now)."""
    removed = 0
    for f in Path(art_dir).glob("*.jpg"):
        if SPLIT_ART.match(f.name) and f.name not in used:
            f.unlink()
            removed += 1
    return removed


def build(book_dir: str | Path, *, force: bool = False, max_side: int = 1400,
          on_progress=None) -> dict:
    book_dir = Path(book_dir).resolve()
    meta = load_book(book_dir)
    out_dir = ensure_dir(book_dir / "reader")
    art_dir = ensure_dir(out_dir / "art")
    cur_path = book_dir / "curation.json"
    curation = json.loads(cur_path.read_text(encoding="utf-8")) if cur_path.exists() else {}
    cpages = curation.get("pages", {})
    text_limit = max(30, min(200, int(curation.get("text_limit", TEXT_LIMIT))))
    max_parts = max(1, min(5, int(curation.get("max_parts", MAX_PARTS))))
    total = len(meta["pages"])

    pages = []
    used_art: set[str] = set()
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
        chunks = _split_text(text, limit=text_limit, max_parts=max_parts)
        boxes = _part_boxes(art, len(chunks), c.get("spots"))
        for ci, chunk in enumerate(chunks):
            sid = pid if len(chunks) == 1 else f"{pid}-{ci + 1}"
            if len(chunks) == 1:
                art_rel, part_w, part_h = f"art/{pid}.jpg", int(art.shape[1]), int(art.shape[0])
                spots = c.get("spots", [])
            else:
                art_rel, part_w, part_h = _save_part_art(art, art_dir, sid, boxes[ci])
                spots = _remap(c.get("spots", []), boxes[ci])
            used_art.add(Path(art_rel).name)
            pages.append({
                "id": sid,
                "source_page": i,
                "part": ci + 1,
                "parts": len(chunks),
                "kind": c.get("kind", "page"),
                "text": chunk,
                "pinyin": pinyin_tokens(chunk),
                "segments": segments(chunk),
                # split pages get their own framing so the reader never shows
                # the same picture twice with different text
                "art": art_rel,
                "art_page": f"art/{pid}.jpg",
                # 2.5D is done with a camera move on the complete plate: cutting
                # printed art into fg/bg layers leaves halos around linework.
                "art_fg": "",
                "art_bg": "",
                "art_w": part_w, "art_h": part_h,
                "scene": c.get("scene", ""),
                # curated words win; generated ones (reader/words/<page>.json,
                # from `book2real words`) fill the rest
                "words": _merge_words(c.get("words", []), _auto_words(out_dir, sid, chunk),
                                      c.get("text", "")),
                "spots": spots,
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
                    art = imread(out_dir / (pg.get("art_page") or pg["art"]))
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
    stale = _prune_part_art(art_dir, used_art)
    if stale:
        data["stats"]["pruned_art"] = stale
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
