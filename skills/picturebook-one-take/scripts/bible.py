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

STYLE_EN = {
    "name": "手绘马克笔 + 水彩，粗黑勾线，暖色纸纹",
    "prompt": ("欧美儿童绘本手绘风格：粗黑马克笔勾线，水彩/彩铅上色，纸纹明显，"
               "暖棕、橙红、灰蓝配色，人物造型夸张可爱，边缘手绘感；"
               "不要3D渲染，不要写实照片，不要日漫赛璐璐，不要光滑塑料质感。"),
    "negative": "3D渲染, 写实照片, 日漫赛璐璐, 光滑塑料质感, 文字, 字幕, 水印, logo, 页码, 边框",
}

NARRATOR = {
    "name": "旁白",
    "voice": "成年女性，幼儿园老师，温暖亲切，慢速，讲故事腔；不要儿童音",
}

def _derive_locations(pages: list, curation: dict, title: str) -> list:
    """没有 per-book 配置时，从 curation 的 scene/地点词推场景；推不出来就整本一个场景。

    这样任何一本新书至少有一个 plate 可生成，不会因为缺配置直接空转。
    """
    groups: dict = {}
    for p in pages:
        pid = p.get("id")
        cur = (curation.get("pages") or {}).get(pid, {}) or {}
        key = str(cur.get("scene") or cur.get("location") or cur.get("location_name") or "").strip()
        if not key:
            continue
        groups.setdefault(key, []).append(pid)
    if groups:
        return [{"id": f"loc{i + 1}", "name": name, "pages": pids}
                for i, (name, pids) in enumerate(groups.items())]
    return [{"id": "main", "name": title or "主场景",
             "pages": [p.get("id") for p in pages if p.get("id")]}]


def _overrides(book_dir: Path) -> dict:
    """per-book 配置：books/<id>/one_take/bible.json（缺省则全走通用推导）。

    以前这些表（角色外观/补充角色/道具/场景）是写死在代码里的，只有 cn01/en01 能跑；
    现在一律从书目录读，代码只保留语言级的通用默认（STYLE_DEFAULT/STYLE_EN）。
    """
    for name in ("bible.json", "cast.json"):
        p = book_dir / "one_take" / name
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


def _load(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default if default is not None else {}


def build_bible(book_dir: str | Path) -> dict:
    book_dir = Path(book_dir).resolve()
    bid = book_dir.name
    reader = _load(book_dir / "reader" / "book.json")
    chars_doc = _load(book_dir / "reader" / "characters" / "characters.json", {"characters": []})
    if not chars_doc.get("characters") and reader.get("characters"):
        chars_doc = {"characters": reader["characters"]}
    curation = _load(book_dir / "curation.json", {})
    ov = _overrides(book_dir)

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

    characters = []
    for c in chars_doc.get("characters", []):
        cid = c["id"]
        name = c.get("name") or cid
        pages_with = [p["id"] for p in pages if cid in (p.get("cast") or [])]
        first_page = min(pages_with) if pages_with else (min(c.get("pages") or ["p99"]))
        default = (ov.get("char_look") or {}).get(cid, {})
        portrait_rel = c.get("portrait") or c.get("image") or ""
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

    for extra in ov.get("char_extra") or []:
        if not any(c["id"] == extra["id"] for c in characters):
            characters.append({**extra, "ref": f"assets/head_{extra['id']}.png"})
    for c in characters:
        override_first = (ov.get("char_first_page") or {}).get(c["id"])
        if override_first:
            c["first_page"] = override_first
    characters = [c for c in characters
                  if c.get("role") or c["id"] in (ov.get("char_look") or {})]
    if not characters:
        seen = {}
        for p in pages:
            for spot in ((curation.get("pages") or {}).get(p["id"], {}) or {}).get("spots") or []:
                name = spot.get("name")
                desc = spot.get("desc") or ""
                if name and any(k in desc for k in ("女孩", "男孩", "男人", "女人", "狗", "girl", "boy", "man", "woman", "dog")):
                    seen.setdefault(name, []).append(p["id"])
        for name, ps in seen.items():
            cid = f"char_{len(characters)+1}"
            characters.append({"id": cid, "name": name, "role": "配角", "desc": "",
                               "look": name, "voice": "温和的童声",
                               "first_page": min(ps), "pages": sorted(set(ps)),
                               "ref": f"assets/head_{cid}.png", "portrait": ""})

    title = curation.get("title") or reader.get("title") or bid
    language = reader.get("language") or "zh"
    style = (ov.get("style")
             or (STYLE_EN if str(language).lower().startswith("en") or bid.startswith("en")
                 else STYLE_DEFAULT))
    props = ov.get("props")
    if not props:
        props, seen = [], set()
        for p in pages:
            for spot in ((curation.get("pages") or {}).get(p["id"], {}) or {}).get("spots") or []:
                nm = spot.get("name") or ""
                if not nm or nm in name_to_id:
                    continue
                if nm not in seen:
                    seen.add(nm)
                    props.append({"id": f"prop_{len(props)+1}", "name": nm,
                                  "look": spot.get("desc") or nm, "pages": [p["id"]]})
                else:
                    for pr in props:
                        if pr["name"] == nm and p["id"] not in pr["pages"]:
                            pr["pages"].append(p["id"])
    bible = {
        "book_id": bid,
        "title": title,
        "language": language,
        "age_range": reader.get("age_range") or "4-8",
        "style": style,
        "narrator": NARRATOR,
        "characters": characters,
        "props": props,
        "pages": pages,
        "locations": ov.get("locations") or _derive_locations(pages, curation, title),
        "target_seconds": ov.get("target_seconds")
        or (180 if str(language).lower().startswith("en") else 150),
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