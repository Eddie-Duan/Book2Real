"""Creative steps that call the three official models.

    story      DeepSeek writes a child-facing retelling per page (+ questions)
    visuals    Seedream fills the "regenerate" split-screen art
    clip       Seedance makes one short clip from a page (demo / probe only)

Everything degrades honestly: without a key the command exits non-zero with a
message naming the missing variable, and no file is written. Generated text is
cached next to the book so a re-run costs nothing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import cloud
from .util import read_json, write_json


def _reader(book_dir: str | Path) -> tuple[Path, dict]:
    book_dir = Path(book_dir).resolve()
    path = book_dir / "reader" / "book.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} 不存在：先跑 python -m book2real analyze --book {book_dir}")
    return book_dir, read_json(path)


def story(book_dir: str | Path, *, force: bool = False, model: str | None = None) -> dict:
    """Ask DeepSeek for one short retelling + two questions per spread.

    The result is *additive*: it never overwrites the book's own text. ``--apply``
    in the CLI can copy the `scene` line into curation.json.
    """
    book_dir, book = _reader(book_dir)
    out = book_dir / "reader" / "story" / "story.json"
    if out.exists() and not force:
        return read_json(out)
    lang = str(book.get("language") or "zh")
    pages = [{k: p.get(k) for k in ("id", "text", "scene")} for p in book.get("pages", [])]
    material = "\n".join(f"{p['id']}: {p.get('text') or ''}" for p in pages)
    if not material.strip():
        raise ValueError("这本书没有可用的正文（先跑 analyze）")
    if lang == "en":
        prompt = (
            "You are a picture-book editor for children aged 4-8. For each page id below, "
            "write one warm, concrete sentence describing what happens (no new facts), and "
            "two short questions a child might ask about that page. Keep the book's original "
            "English. Return only JSON: {\"pages\":[{\"id\":\"p01\",\"scene\":\"...\","
            "\"questions\":[\"...\",\"...\"]}]}.\n\n" + material)
    else:
        prompt = (
            "你是 4-8 岁绘本编辑。对下面每一页，写一句具体、温暖的画面说明（不添加原书没有的事实），"
            "再写两个孩子可能会问的问题。只输出 JSON："
            "{\"pages\":[{\"id\":\"p01\",\"scene\":\"...\",\"questions\":[\"...\",\"...\"]}]}。\n\n"
            + material)
    res = cloud.deepseek([{"role": "user", "content": prompt}], model=model,
                         temperature=0.5, max_tokens=2000, json_mode=True)
    try:
        data = json.loads(res["text"])
    except Exception as exc:                       # noqa: BLE001
        raise cloud.CloudError(f"DeepSeek 返回不是 JSON：{res['text'][:200]}") from exc
    rows = []
    for item in data.get("pages") or []:
        pid = str(item.get("id") or "").strip()
        if not pid:
            continue
        rows.append({"id": pid, "scene": str(item.get("scene") or "").strip(),
                     "questions": [str(q).strip() for q in (item.get("questions") or [])][:3]})
    payload = {"book_id": book.get("book_id"), "language": lang,
               "model": res.get("model"), "cny": res.get("cny"), "pages": rows}
    write_json(out, payload)
    return payload


def visuals(book_dir: str | Path, *, force: bool = False, limit: int = 0,
            model: str | None = None) -> list[dict]:
    """Generate the split-screen art that storybook marked ``regenerate``."""
    book_dir, book = _reader(book_dir)
    art = book_dir / "reader" / "art"
    done = []
    for page in book.get("pages", []):
        vis = page.get("visual") or {}
        if vis.get("type") != "regenerate":
            continue
        prompt = (vis.get("prompt") or "").strip()
        if not prompt:
            continue
        out = art / f"{page['id']}-gen.jpg"
        if out.exists() and not force:
            continue
        r = cloud.seedream(prompt, out, model=model, size="1024x1024")
        done.append({"page": page["id"], "path": str(out), "cny": r.get("cny")})
        if limit and len(done) >= limit:
            break
    return done


def clip(book_dir: str | Path, page_id: str, prompt: str, out: str | Path, *,
         seconds: int = 5, model: str | None = None, image: bool = True) -> dict:
    """One Seedance clip for a page. This is a *probe*, not the submission film.

    Q43 forbids stitching separately generated shots into the delivered book
    film, so the official film path stays single-pass (`book2real film`).
    """
    book_dir, book = _reader(book_dir)
    first = None
    if image:
        for p in book.get("pages", []):
            if p.get("id") == page_id:
                first = book_dir / "reader" / p["art"]
                break
    return cloud.seedance(prompt, out, image=first, model=model, seconds=seconds,
                          resolution="720p", ratio="16:9")


def apply_scenes(book_dir: str | Path) -> int:
    """Copy generated scenes into curation.json (the only hand-editable layer)."""
    book_dir, _ = _reader(book_dir)
    src = book_dir / "reader" / "story" / "story.json"
    if not src.exists():
        raise FileNotFoundError(src)
    data = read_json(src)
    cj = book_dir / "curation.json"
    cur = read_json(cj) if cj.exists() else {"pages": {}}
    pages = cur.setdefault("pages", {})
    n = 0
    for row in data.get("pages") or []:
        if row.get("scene"):
            pages.setdefault(row["id"], {})["scene"] = re.sub(r"\s+", " ", row["scene"]).strip()
            n += 1
    write_json(cj, cur)
    return n
