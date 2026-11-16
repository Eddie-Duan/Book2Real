"""绘本入库：PDF → 页图 + 文字/OCR + 脚本文本 + 可点击元素卡。"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Iterable, Optional

import fitz

from ..config import Settings, settings as default_settings
from ..utils import detect_lang, normalize_for_tokens, normalize_text, truncate
from .aliases import expand_query
from .ocr import extract_page, looks_scanned

log = logging.getLogger(__name__)

NUM_RE = re.compile(r"(\d{1,3})")
REQUIRED_RE = re.compile(r"[（(]\s*必选\s*[）)]")
ITEM_RE = re.compile(r"^\s*(\d{1,2})[.、]\s*(.+?)\s*$")
CLICK_RE = re.compile(r"^\s*\[点击\]\s*(.+?)\s*$")
QUOTED_RE = re.compile(r"[“\"「']([^”\"」']+)[”\"」']")
BOOK_NAME_RE = re.compile(r"(中文|英文)绘本\s*0?(\d+)")


def _strip_required(text: str) -> tuple[str, bool]:
    required = bool(REQUIRED_RE.search(text))
    return REQUIRED_RE.sub("", text).strip(), required


def parse_script(raw: str) -> dict:
    items: list[dict] = []
    current: dict | None = None
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        click_match = CLICK_RE.match(line)
        if click_match:
            body, required = _strip_required(click_match.group(1))
            target, _, action = body.partition("|")
            target = re.sub(r"^点击\s*", "", target).strip()
            click = {"target": target, "action": action.strip(), "required": required}
            if current is not None:
                current["click"] = click
                current["type"] = "click"
            else:
                items.append({"no": len(items) + 1, "text": body, "type": "click", "click": click, "required": required})
            continue
        match = ITEM_RE.match(line)
        if not match:
            continue
        no = int(match.group(1))
        text, required = _strip_required(match.group(2))
        item: dict = {"no": no, "text": text, "required": required, "type": "question"}
        if re.match(r"^(播放|暂停|继续|结束播放|play|pause|resume|stop)\b", text, re.I):
            item["type"] = "playback"
        elif text.startswith(("找到", "Find", "Show me", "Look for")):
            item["type"] = "find_scene"
            q = re.sub(r"^(找到|Find|Show me|Look for)\s*", "", text, flags=re.I)
            q = re.sub(r"(的)?画面.*$", "", q).strip() or text
            item["scene_query"] = q
        elif text.startswith(("查找", "高亮", "Find the text", "Highlight")):
            item["type"] = "find_text"
            quoted = QUOTED_RE.search(text)
            q = quoted.group(1) if quoted else re.sub(r"^(查找文字|查找|高亮文字|高亮|Find the text|Highlight)\s*", "", text, flags=re.I).strip()
            item["text_query"] = q
        items.append(item)
        current = item
    return {"items": items, "raw": raw}


def book_id_from_name(name: str) -> tuple[str, str]:
    match = BOOK_NAME_RE.search(name)
    if match:
        lang = "zh" if match.group(1) == "中文" else "en"
        return f"{'cn' if lang == 'zh' else 'en'}{int(match.group(2)):02d}", lang
    lang = detect_lang(name, "zh")
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", name).strip("-").lower() or "book"
    return slug, lang


def _page_score(page: dict, query: str) -> float:
    q = normalize_for_tokens(query)
    if not q.strip():
        return 0.0
    story = normalize_for_tokens(page.get("story_text", ""))
    teaching = normalize_for_tokens(page.get("teaching_text", ""))
    text = story + " " + teaching
    if not text.strip():
        return 0.0

    def text_score(body: str, weight: float) -> float:
        if not body:
            return 0.0
        score = 0.0
        if q in body:
            score += 10.0 + min(5.0, len(q) / max(1, len(body)) * 100)
        tokens = set(q[i : i + 2] for i in range(max(0, len(q) - 1)))
        if tokens:
            hits = sum(1 for t in tokens if t in body)
            score += hits / max(1, len(tokens))
        return score * weight

    score = text_score(story, 1.0)
    # 新场景通常出现在该页第一句：给首句命中一点加权，避免被后页的重复词吸走
    raw_story = page.get("story_text", "") or ""
    first = normalize_for_tokens(re.split(r"[。！？!?]", raw_story)[0]) if raw_story else ""
    if first and q:
        if q in first:
            score += 0.5
        else:
            tokens = set(q[i : i + 2] for i in range(max(0, len(q) - 1)))
            if tokens:
                score += 0.35 * sum(1 for t in tokens if t in first) / max(1, len(tokens))
    # 场景语义纠偏：搭积木的提问不应匹配到“倒塌”页；问倒塌不应匹配“没有倒”页
    if "搭" in q and ("一倒" in text or "倒了" in text or "倒塌" in text):
        score -= 2.2
    if "倒" in q and "没有倒" in text:
        score -= 2.2
    return max(0.0, score)


def _find_page(pages: list[dict], query: str) -> Optional[int]:
    best_no, best = None, 0.0
    for page in pages:
        score = _page_score(page, query)
        if score > best:
            best_no, best = page["no"], score
    return best_no if best >= 0.34 else None


def _find_block(page: dict, target: str) -> Optional[list[float]]:
    if not page:
        return None
    candidates = [target]
    for suffix in ("右眼", "左眼", "头部", "身上的小老鼠", "翅膀", "眼睛", "嘴巴"):
        if target.endswith(suffix) and len(target) > len(suffix):
            candidates.append(target[: -len(suffix)])
    for cand in candidates:
        for block in page.get("blocks", []):
            text = block.get("text", "")
            if cand and cand in text:
                return block.get("bbox")
    return None


def _find_sentence(pages: list[dict], target: str) -> str:
    for page in pages:
        text = page.get("story_text", "")
        for sent in re.split(r"(?<=[。！？!?；;])", text):
            if target and target in sent:
                return sent.strip()
    return ""


def _build_elements(pages: list[dict], script: dict) -> list[dict]:
    elements: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for item in script.get("items", []):
        click = item.get("click")
        if not click:
            continue
        target = click.get("target") or item.get("text", "")
        base = re.split(r"[的右左]|身上", target)[0] or target
        page_no = int(item.get("page") or 1)
        page = next((p for p in pages if p["no"] == page_no), None)
        key = (target, page_no)
        if key in seen:
            continue
        seen.add(key)
        info = _find_sentence(pages, target) or _find_sentence(pages, base)
        if not info and page:
            info = (page.get("story_text") or "").split("。")[0]
        elements.append(
            {
                "name": target,
                "page": page_no,
                "bbox": _find_block(page, target),
                "info": truncate(info.strip("“”\"' "), 100) or f"{target}是这本绘本里出现的一个角色或物品。",
                "action": click.get("action", ""),
                "required": click.get("required", False),
                "script_no": item.get("no"),
            }
        )
    return elements


def _item_page_scores(pages: list[dict], item: dict, lang: str) -> list[float]:
    fields: list[tuple[str, float]] = []
    if item.get("scene_query"):
        fields.append((str(item["scene_query"]), 1.0))
    if item.get("text_query"):
        fields.append((str(item["text_query"]), 1.0))
    if item.get("click"):
        fields.append((str(item["click"].get("target") or ""), 1.2))
    if item.get("text"):
        fields.append((str(item["text"]), 0.55))
    row: list[float] = []
    cover_query = any(
        str(field).lower().find(k) >= 0
        for field, _ in fields
        for k in ("封面", "书名", "title", "cover")
    )
    for page in pages:
        best = 0.0
        for field, weight in fields:
            if field:
                best = max(best, _page_score(page, expand_query(field, lang)) * weight)
        if int(page.get("no") or 0) == 1 and not cover_query:
            best *= 0.5  # 封面不是正文场景
        row.append(0.02 + best)
    return row


def _map_script_pages(pages: list[dict], script: dict, lang: str = "zh") -> None:
    """单调动态规划：脚本按故事顺序推进，给每个动作找最合理的页码。

    比逐条独立匹配稳定得多：既能利用强关键词，也能让弱匹配继承相邻动作的页码；
    重复点击对象（如开头/结尾各出现一次的“积木城堡”）会被自然分到不同页。
    """
    items = script.get("items", [])
    if not items or not pages:
        return
    n = len(pages)
    scores = [_item_page_scores(pages, item, lang) for item in items]
    if n:
        scores[0][0] += 1.5  # 片头动作从第 1 页开始
        scores[-1][n - 1] += 1.5  # 片尾动作落在最后一页
        for i in range(min(3, len(scores))):
            scores[i][0] += 0.8
    dp = [[-1e18] * n for _ in items]
    prev = [[-1] * n for _ in items]
    for p in range(n):
        dp[0][p] = scores[0][p]
    for i in range(1, len(items)):
        is_scene = bool(items[i].get("scene_query"))
        for p in range(n):
            best, best_q = -1e18, -1
            for q in range(p + 1):
                advance = p - q
                bonus = 0.0
                if is_scene and advance > 0:
                    bonus = 0.25 if advance == 1 else 0.10
                val = dp[i - 1][q] + (0.15 if advance == 0 else 0.0) - 0.08 * advance + bonus
                if val > best:
                    best, best_q = val, q
            dp[i][p] = scores[i][p] + best
            prev[i][p] = best_q
    p = max(range(n), key=lambda x: dp[-1][x])
    assignment = [0] * len(items)
    for i in range(len(items) - 1, -1, -1):
        assignment[i] = p + 1
        if i > 0:
            p = prev[i][p]
    for item, page_no in zip(items, assignment):
        item["page"] = page_no


def ingest_pdf(
    pdf_path: str | Path,
    book_id: str | None = None,
    title: str | None = None,
    lang: str | None = None,
    script_path: str | Path | None = None,
    settings: Settings | None = None,
    force: bool = False,
) -> dict:
    settings = settings or default_settings
    pdf_path = Path(pdf_path)
    stem = pdf_path.stem
    auto_id, auto_lang = book_id_from_name(stem)
    book_id = book_id or auto_id
    lang = lang or auto_lang
    book_dir = settings.books_dir / book_id
    book_json = book_dir / "book.json"
    if book_json.exists() and not force:
        return json.loads(book_json.read_text(encoding="utf-8"))

    pages_dir = book_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    doc = fitz.open(str(pdf_path))
    pages: list[dict] = []
    cover_text = ""
    for idx, page in enumerate(doc):
        no = idx + 1
        pix = page.get_pixmap(dpi=settings.ocr_dpi)
        image_path = pages_dir / f"{no:03d}.png"
        pix.save(str(image_path))
        info = extract_page(page, settings.ocr_dpi, image_path, lang, settings)
        info.update({"no": no, "image": f"/api/books/{book_id}/pages/{no}", "width": pix.width, "height": pix.height})
        pages.append(info)
        if no == 1 and not cover_text:
            cover_text = info.get("story_text", "")
    doc.close()

    script = {"items": [], "raw": ""}
    if script_path and Path(script_path).is_file():
        script = parse_script(Path(script_path).read_text(encoding="utf-8", errors="replace"))
    _map_script_pages(pages, script, lang or "zh")
    elements = _build_elements(pages, script)

    if not title:
        cover_lines = [ln.strip() for ln in re.split(r"[\n。！？]", cover_text) if 2 <= len(ln.strip()) <= 24]
        title = cover_lines[0] if cover_lines else stem
    all_text = "\n".join(p.get("story_text", "") for p in pages)
    detected = lang or detect_lang(all_text)
    book = {
        "id": book_id,
        "title": title,
        "lang": detected,
        "source_pdf": str(pdf_path),
        "page_count": len(pages),
        "dpi": settings.ocr_dpi,
        "created_at": time.time(),
        "ingest_seconds": round(time.time() - t0, 1),
        "pages": pages,
        "script": script,
        "elements": elements,
        "summary": {
            "page_count": len(pages),
            "story_pages": sum(1 for p in pages if p.get("story_text")),
            "ocr_pages": sum(1 for p in pages if p.get("ocr")),
            "click_elements": len(elements),
            "text_chars": len(all_text),
        },
    }
    book_dir.mkdir(parents=True, exist_ok=True)
    book_json.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("ingested %s: %s pages, %.1fs", book_id, len(pages), time.time() - t0)
    return book


def ingest_materials_zip(zip_path: str | Path, settings: Settings, force: bool = False) -> list[dict]:
    """解压赛事素材，入库 6 本绘本，并把 Query 音频放到每本书的 queries/。"""
    zip_path = Path(zip_path)
    settings.ensure_dirs()
    extract_dir = settings.source_dir / "competition"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    books: list[dict] = []
    for pdf in sorted(extract_dir.rglob("*.pdf")):
        if "绘" not in str(pdf.parent) and "绘本" not in pdf.name:
            continue
        book_id, lang = book_id_from_name(pdf.stem)
        script_path = pdf.with_suffix(".txt")
        book = ingest_pdf(pdf, book_id=book_id, title=pdf.stem, lang=lang, script_path=script_path if script_path.exists() else None, settings=settings, force=force)
        # 复制 Query 音频（文件名编号 ↔ 脚本编号）
        audio_dir = pdf.parent / pdf.stem
        if audio_dir.is_dir():
            target_dir = settings.books_dir / book_id / "queries"
            target_dir.mkdir(parents=True, exist_ok=True)
            by_no = {str(i["no"]).zfill(2): i for i in book.get("script", {}).get("items", [])}
            for wav in sorted(audio_dir.glob("*.wav")):
                num = NUM_RE.search(wav.stem)
                if not num:
                    continue
                no = int(num.group(1))
                dest = target_dir / f"{no:02d}.wav"
                shutil.copyfile(wav, dest)
                item = by_no.get(f"{no:02d}")
                if item is not None:
                    item["audio"] = f"/api/books/{book_id}/queries/{no:02d}.wav"
        (settings.books_dir / book_id / "book.json").write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
        books.append(book)
    return books


def list_books(settings: Settings) -> list[dict]:
    out = []
    for path in sorted(settings.books_dir.glob("*/book.json")):
        try:
            book = json.loads(path.read_text(encoding="utf-8"))
            out.append({"id": book["id"], "title": book.get("title", book["id"]), "lang": book.get("lang", "zh"), "page_count": book.get("page_count", 0), "summary": book.get("summary", {})})
        except Exception:
            continue
    return out
