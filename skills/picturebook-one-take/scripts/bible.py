# -*- coding: utf-8 -*-
"""Build film_bible.json from an imported book (books/<id>)."""
from __future__ import annotations

import json
from pathlib import Path

from common import ROOT, jwrite

STYLE_DEFAULT = {
    "name": "中国儿童绘本手绘水彩 + 彩铅勾线",
    "prompt": ("中国儿童绘本手绘水彩风格，彩铅勾线，纸纹，柔和晕染，暖色淡彩，"
               "手绘笔触，边缘自然，配色温柔；不要3D渲染，不要写实照片，不要动漫赛璐璐，"
               "不要厚涂，不要塑料质感。"),
    "negative": "3D渲染, 写实照片, 动漫赛璐璐, 厚涂, 塑料质感, 文字, 字幕, 水印, logo, 页码, 边框",
}

CHAR_DEFAULTS = {
    "huahua": {"look": "圆脸，两个小丸子头，红色小花发卡，粉色上衣，粉白条纹短裤，五六岁",
               "voice": "稚嫩清亮的小女孩声音，语速稍快"},
    "xiaoman": {"look": "圆脸，小马尾，蘑菇发夹，橙色背带裤，五六岁",
                "voice": "软一点的小女孩声音，语速稍慢"},
    "huangmituan": {"look": "小黄狗，红项圈，短腿，尾巴卷起",
                    "voice": "只发出汪汪/呜呜声，不说话"},
}


def _load(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default if default is not None else {}


def build_bible(book_dir: str | Path) -> dict:
    book_dir = Path(book_dir).resolve()
    bid = book_dir.name
    reader = _load(book_dir / "reader" / "book.json")
    chars_doc = _load(book_dir / "reader" / "characters" / "characters.json", {"characters": []})
    curation = _load(book_dir / "curation.json", {})

    # page text + scene + cast from curation spots
    pages = []
    name_to_id = {}
    for c in chars_doc.get("characters", []):
        if c.get("name"):
            name_to_id[c["name"]] = c["id"]
    for p in reader.get("pages", []):
        pid = p.get("id")
        cur = (curation.get("pages") or {}).get(pid, {})
        cast = []
        for spot in cur.get("spots") or []:
            cid = name_to_id.get(spot.get("name"))
            if cid and cid not in cast:
                cast.append(cid)
        pages.append({
            "id": pid,
            "text": (p.get("text") or "").strip(),
            "scene": cur.get("scene") or "",
            "kind": cur.get("kind") or ("cover" if pid == "p01" else "page"),
            "cast": cast,
        })

    # characters: first page from spots first, then characters.json
    characters = []
    for c in chars_doc.get("characters", []):
        cid = c["id"]
        name = c.get("name") or cid
        pages_with = [p["id"] for p in pages if cid in (p.get("cast") or [])]
        first_page = min(pages_with) if pages_with else (min(c.get("pages") or ["p99"]))
        default = CHAR_DEFAULTS.get(cid, {})
        portrait_rel = c.get("portrait") or ""
        portrait_full = (book_dir / "reader" / portrait_rel).resolve() if portrait_rel else None
        if portrait_full and portrait_full.exists():
            portrait_rel = str(portrait_full.relative_to(ROOT))
        else:
            portrait_rel = ""
        characters.append({
            "id": cid,
            "name": name,
            "role": c.get("role") or "",
            "desc": c.get("desc") or "",
            "look": default.get("look") or c.get("desc") or "",
            "voice": default.get("voice") or "温和的童声",
            "first_page": first_page,
            "pages": sorted(set(pages_with) | set(c.get("pages") or [])),
            "ref": f"assets/head_{cid}.png",
            "portrait": portrait_rel,
        })

    # only keep real characters (drop objects mistakenly cast as characters)
    characters = [c for c in characters if c.get("role") in ("主角", "小狗", "配角") or c["id"] in CHAR_DEFAULTS]
    if not characters:
        # fallback: derive from curation named spots with person-like descriptions
        seen = {}
        for p in pages:
            for spot in ((curation.get("pages") or {}).get(p["id"], {}) or {}).get("spots") or []:
                name = spot.get("name")
                if name and ("女孩" in (spot.get("desc") or "") or "狗" in (spot.get("desc") or "")):
                    seen.setdefault(name, []).append(p["id"])
        for name, ps in seen.items():
            cid = f"char_{len(characters)+1}"
            characters.append({"id": cid, "name": name, "role": "配角", "desc": "",
                               "look": name, "voice": "温和的童声",
                               "first_page": min(ps), "pages": sorted(set(ps)),
                               "ref": f"assets/head_{cid}.png"})

    title = curation.get("title") or reader.get("title") or bid
    bible = {
        "book_id": bid,
        "title": title,
        "language": reader.get("language") or "zh",
        "age_range": reader.get("age_range") or "4-8",
        "style": STYLE_DEFAULT,
        "characters": characters,
        "pages": pages,
        "locations": [
            {"id": "room_rain", "name": "下雨天的儿童房", "pages": ["p02", "p03", "p04", "p05", "p06"]},
            {"id": "room_home", "name": "花花家的房间", "pages": ["p07", "p08"]},
            {"id": "meadow", "name": "春天的草地", "pages": ["p07"]},
            {"id": "birthday_room", "name": "生日房间", "pages": ["p08", "p09"]},
            {"id": "door", "name": "门口/院子里", "pages": ["p10", "p11"]},
            {"id": "final", "name": "草地上/重建的城堡", "pages": ["p12"]},
        ],
        "target_seconds": 150,
        "source": {
            "book_json": str((book_dir / "reader" / "book.json").relative_to(ROOT)),
            "curation": str((book_dir / "curation.json").relative_to(ROOT)),
        },
    }
    return bible


def cli_bible(book: str, out: str) -> dict:
    bible = build_bible(book)
    jwrite(Path(out) / "bible.json", bible)
    return bible
