# -*- coding: utf-8 -*-
"""DeepSeek vision book analysis: style / characters / props / locations.

The agent should not hard-code what a book contains. Send representative page
images to deepseek-flash (vision-capable), get structured JSON, merge into the
bible. Book.json characters stay authoritative; vision only adds style/props/
locations and fills missing looks.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import cv2

from common import DEEPSEEK, DEEPSEEK_KEY, DEEPSEEK_MODEL, http_json, jread, jwrite

SCHEMA = """只输出 JSON，不要解释：
{"style":{"medium":"","line":"","palette":"","texture":"","summary":""},
 "characters":[{"name":"","look":"","role":"","screen_only":false,"on_screen":true}],
 "props":[{"name":"","look":""}],
 "location":{"name":"","look":""},
 "scene":"一句话描述这一页的可拍画面",
 "text_present":true}"""


def _img_data(path: Path, max_w: int = 900) -> str:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(path)
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def analyze_page(page: dict, image: Path, language: str) -> dict:
    user = [
        {"type": "text", "text": (
            f"这是儿童绘本的一页，页码 {page['id']}。页上文字：{page.get('text') or '(无字)'}\n"
            "请识别：整体画风（媒介/勾线/配色/纸纹）、现实角色（不是海报或银幕里的虚构角色）、"
            "关键道具（颜色+形状要稳定）、地点、这一页适合拍成的画面。\n" + SCHEMA)},
        {"type": "image_url", "image_url": {"url": _img_data(image)}},
    ]
    status, data = http_json("POST", DEEPSEEK + "/v1/chat/completions", {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": user}],
        "temperature": 0.2, "max_tokens": 900,
        "response_format": {"type": "json_object"}}, DEEPSEEK_KEY, timeout=120)
    if status != 200:
        raise RuntimeError(f"DeepSeek vision HTTP {status}: {json.dumps(data, ensure_ascii=False)[:400]}")
    return json.loads(data["choices"][0]["message"]["content"])


def _sample_pages(bible: dict, max_pages: int) -> list[dict]:
    pages = bible.get("pages", [])
    if len(pages) <= max_pages:
        return pages
    # cover + pages with cast + evenly spaced
    pick = []
    for p in pages:
        if p.get("kind") == "cover" or p.get("cast"):
            pick.append(p)
    for i in range(max_pages):
        p = pages[round(i * (len(pages) - 1) / max(1, max_pages - 1))]
        if p not in pick:
            pick.append(p)
    # keep first max_pages, but always include cover
    pick = [p for p in pick if p.get("kind") == "cover"] + [p for p in pick if p.get("kind") != "cover"]
    return pick[:max_pages]


def analyze_book(bible: dict, out: str | Path, max_pages: int = 8) -> dict:
    book_id = bible["book_id"]
    art = Path("books") / book_id / "reader" / "art"
    results = []
    for p in _sample_pages(bible, max_pages):
        img = art / f"{p['id']}.jpg"
        if not img.exists():
            continue
        try:
            r = analyze_page(p, img, bible.get("language", "zh"))
            r["_page"] = p["id"]
            results.append(r)
            print(f"  vision {p['id']}: {r.get('scene','')[:40]}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  vision {p['id']} failed: {exc}", flush=True)

    if not results:
        raise RuntimeError("vision analysis returned nothing")

    # style: concatenate unique summaries and let DS synthesise one prompt
    styles = [r.get("style", {}) for r in results]
    style_prompt = ""
    try:
        st, data = http_json("POST", DEEPSEEK + "/v1/chat/completions", {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": (
                "把下面的绘本画风描述合并成一条可直接用于图像/视频生成的中文画风提示词，"
                "包含媒介、勾线、配色、纸纹、质感，并写不要什么。只输出提示词。\n" +
                json.dumps(styles, ensure_ascii=False))}],
            "temperature": 0.2, "max_tokens": 300}, DEEPSEEK_KEY, timeout=120)
        if st == 200:
            style_prompt = data["choices"][0]["message"]["content"].strip()
    except Exception:  # noqa: BLE001
        pass
    if not style_prompt:
        s = styles[0]
        style_prompt = "；".join(str(s.get(k) or "") for k in ("medium", "line", "palette", "texture", "summary"))

    # characters: merge by lower-case name, skip screen-only
    chars: dict[str, dict] = {}
    for r in results:
        for c in r.get("characters") or []:
            name = str(c.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if c.get("screen_only"):
                continue
            if key not in chars:
                chars[key] = {"name": name, "look": c.get("look") or "", "role": c.get("role") or "",
                              "pages": [r["_page"]], "screen_only": False}
            else:
                chars[key]["pages"].append(r["_page"])
                if not chars[key]["look"] and c.get("look"):
                    chars[key]["look"] = c["look"]
    # only recurring real characters (>=2 pages) unless book.json has them
    known = {c["id"]: c for c in bible.get("characters", [])}
    known_names = {c["name"].lower() for c in bible.get("characters", [])}
    auto_chars = [c for k, c in chars.items() if k in known_names or len(set(c["pages"])) >= 2]

    # props: merge by name
    props: dict[str, dict] = {}
    for r in results:
        for p in r.get("props") or []:
            name = str(p.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key not in props:
                props[key] = {"id": f"prop_{len(props)+1}", "name": name,
                              "look": p.get("look") or name, "pages": [r["_page"]]}
            else:
                props[key]["pages"].append(r["_page"])
                if not props[key]["look"] or len(props[key]["look"]) < len(p.get("look") or ""):
                    props[key]["look"] = p.get("look")

    # locations: one per page, grouped by name
    locs: dict[str, dict] = {}
    for r in results:
        loc = r.get("location") or {}
        name = str(loc.get("name") or "").strip() or r["_page"]
        key = name.lower()
        if key not in locs:
            locs[key] = {"id": f"loc_{len(locs)+1}", "name": name,
                         "look": loc.get("look") or "", "pages": [r["_page"]]}
        else:
            locs[key]["pages"].append(r["_page"])

    vision = {
        "book_id": book_id,
        "style": {"name": "vision", "prompt": style_prompt,
                  "negative": "文字, 字幕, 水印, logo, 页码, 边框, 3D渲染, 写实照片"},
        "characters": auto_chars,
        "props": list(props.values()),
        "locations": list(locs.values()),
        "pages": {r["_page"]: {"scene": r.get("scene"), "text_present": r.get("text_present")}
                  for r in results},
        "raw": results,
    }
    jwrite(Path(out) / "vision_bible.json", vision)
    return vision


def merge_into_bible(bible: dict, vision: dict) -> dict:
    """Vision owns style; book.json owns identity.

    DS often confuses the boy and the dad (both red hair) and labels background
    film characters as cast. So: never add auto characters when the book already
    has a cast; never overwrite an existing look/desc. Props/locations are merged
    with book definitions first, vision as supplement.
    """
    b = dict(bible)
    b["characters"] = [c for c in b.get("characters", []) if not c["id"].startswith("auto_")]
    b["style"] = vision["style"]
    b["vision_characters"] = vision.get("characters") or []

    # keep book characters exactly; only fill an empty look from a same-name match
    vmap = {c["name"].lower(): c for c in vision.get("characters", [])}
    for c in b.get("characters", []):
        v = vmap.get(c["name"].lower())
        if v and v.get("look") and not c.get("look") and not c.get("desc"):
            c["look"] = v["look"]

    # props: book props first, then vision props not already present; keep <=8 by page count
    book_props = list(b.get("props") or [])
    seen = {p["name"].lower() for p in book_props}
    extra = []
    for p in vision.get("props") or []:
        key = str(p.get("name") or "").lower()
        if not key or key in seen:
            continue
        # drop non-visual / background-only entries
        if any(k in key for k in ("存钱", "未直接", "放映光", "灯架")):
            continue
        seen.add(key)
        extra.append(p)
    extra.sort(key=lambda p: len(set(p.get("pages") or [])), reverse=True)
    b["props"] = (book_props + extra)[:8]

    # locations: book locations win; vision only supplements when book has none
    if not b.get("locations"):
        b["locations"] = vision.get("locations") or []
    b["vision_pages"] = vision.get("pages") or {}
    return b