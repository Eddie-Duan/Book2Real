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

CHAR_DEFAULTS = {
    "huahua": {"look": "圆脸，两个小丸子头，红色小花发卡，粉色上衣，粉白条纹短裤，五六岁",
               "voice": "五六岁女孩，音调偏高，活泼，句尾上扬"},
    "xiaoman": {"look": "圆脸，小马尾，蘑菇发夹，橙色背带裤，五六岁",
                "voice": "五六岁女孩，音调稍低，软糯，慢半拍"},
    "huangmituan": {"look": "小黄狗，红项圈，短腿，尾巴卷起",
                    "voice": "只发出汪汪/呜呜声，不说话"},
    "kuan": {"look": "七八岁男孩，红色刺猬头，蓝色T恤，黄色领结，抱着爆米花桶",
             "voice": "七八岁男孩，清亮，语速中等，情绪外放"},
    "dad": {"look": "成年男性，红色卷发，戴眼镜，绿色开衫，温和",
            "voice": "成年男性，低沉温和，语速慢"},
}

CHAR_EXTRA_BY_BOOK = {
    "en01": [
        {"id": "dog", "name": "斑点狗", "role": "宠物",
         "desc": "Kuan 家的白底黑斑大狗，垂耳，红舌头，出现在家里的餐桌旁。",
         "look": "白底黑斑大狗，垂耳，红舌头，体型大", "voice": "只发出汪汪/呜呜声，不说话",
         "first_page": "p26", "pages": ["p26"], "portrait": ""},
    ],
}

PROPS_BY_BOOK = {
    "cn01": [
        {"id": "blocks", "name": "积木", "pages": ["p02", "p03", "p04", "p05", "p06", "p12"],
         "ref": "assets/plate_room_1280x720.png", "look": "彩色木制积木：红/黄/绿/蓝，长方体、三角、拱形、小门、小窗，约二十块"},
        {"id": "castle", "name": "积木城堡", "pages": ["p02", "p03", "p04", "p05", "p12"],
         "ref": "assets/plate_room_1280x720.png", "look": "红黄绿蓝积木搭成的城堡，红色三角屋顶，紫色拱形门，绿色拱形墙"},
        {"id": "cake", "name": "生日蛋糕", "pages": ["p08", "p09", "p11"],
         "ref": "assets/plate_birthday_1280x720.png", "look": "白色奶油蛋糕，粉色花边，红色草莓，放在木桌上"},
        {"id": "card", "name": "生日卡片", "pages": ["p09", "p10", "p11"],
         "look": "对折的彩色手工卡片，画着两个小女孩和积木城堡"},
        {"id": "envelope", "name": "小信封", "pages": ["p09", "p10"],
         "look": "浅色小信封，被小狗叼在嘴里"},
        {"id": "ladybug", "name": "小瓢虫", "pages": ["p07"],
         "look": "红黑小瓢虫，圆点，停在绿叶上"},
    ],
    "en01": [
        {"id": "popcorn", "name": "爆米花", "pages": ["p07", "p08", "p13", "p14", "p22"],
         "ref": "assets/plate_lobby_1280x720.png", "look": "红白条纹爆米花桶，装满黄色爆米花"},
        {"id": "robot", "name": "蓝色机器人", "pages": ["p07", "p08", "p17"],
         "ref": "assets/plate_lobby_1280x720.png", "look": "大号浅蓝色圆头机器人玩具，圆眼睛，短腿，抱在 Kuan 怀里"},
        {"id": "coins", "name": "硬币", "pages": ["p16", "p18"],
         "look": "几枚金色硬币，放在手心/柜台上"},
        {"id": "seats", "name": "影院座椅", "pages": ["p01", "p07", "p20", "p23"],
         "ref": "assets/plate_auditorium_1280x720.png", "look": "深红色绒面影院座椅，成排，靠背高"},
        {"id": "posters", "name": "电影海报", "pages": ["p02", "p03"],
         "look": "墙上的三张抽象电影海报：几何色块、光晕、无真人、无汽车品牌、无可读文字"},
        {"id": "snacks", "name": "零食", "pages": ["p10", "p13", "p18"],
         "look": "薯片、糖果、爆米花、彩色包装，摆在柜台/腿上"},
    ],
}

LOCATIONS_BY_BOOK = {
    "cn01": [
        {"id": "room_rain", "name": "下雨天的儿童房", "pages": ["p02", "p03", "p04", "p05", "p06"]},
        {"id": "room_home", "name": "花花家的房间", "pages": ["p07", "p08"]},
        {"id": "meadow", "name": "春天的草地", "pages": ["p07"]},
        {"id": "birthday_room", "name": "生日房间", "pages": ["p08", "p09"]},
        {"id": "door", "name": "门口/院子里", "pages": ["p10", "p11"]},
        {"id": "final", "name": "草地上/重建的城堡", "pages": ["p12"]},
    ],
    "en01": [
        {"id": "cinema_front", "name": "电影院门厅/海报墙", "pages": ["p01", "p02", "p03"]},
        {"id": "auditorium", "name": "影院放映厅", "pages": ["p07", "p08", "p20", "p23"]},
        {"id": "lobby", "name": "影院大堂/零食柜台", "pages": ["p10", "p13", "p14", "p18"]},
        {"id": "screen", "name": "银幕里的电影世界", "pages": ["p04", "p05", "p24", "p25"]},
        {"id": "home_table", "name": "家里的餐桌", "pages": ["p26", "p27", "p28"]},
    ],
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
    if not chars_doc.get("characters") and reader.get("characters"):
        chars_doc = {"characters": reader["characters"]}
    curation = _load(book_dir / "curation.json", {})

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
        default = CHAR_DEFAULTS.get(cid, {})
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

    for extra in CHAR_EXTRA_BY_BOOK.get(bid, []):
        if not any(c["id"] == extra["id"] for c in characters):
            characters.append({**extra, "ref": f"assets/head_{extra['id']}.png"})
    for c in characters:
        if bid == "en01" and c["id"] == "dad":
            c["first_page"] = "p01"
    characters = [c for c in characters if c.get("role") or c["id"] in CHAR_DEFAULTS]
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
    style = STYLE_EN if str(language).lower().startswith("en") or bid.startswith("en") else STYLE_DEFAULT
    props = PROPS_BY_BOOK.get(bid)
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
        "locations": LOCATIONS_BY_BOOK.get(bid, []),
        "target_seconds": 180 if bid.startswith("en") else 150,
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