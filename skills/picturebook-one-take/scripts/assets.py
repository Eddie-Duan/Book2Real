# -*- coding: utf-8 -*-
"""Seedream 5.5 production plates + character headshots.

Hard rule: plates are full-bleed 16:9 illustrations generated from book references,
never raw page crops with panels/text/watermark.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2

from common import (ARK, PRICE_IMAGE_CNY, ROOT, SEEDREAM_MODEL, ark_post, data_url,
                    download, jwrite, ledger_append, now)

# cn01-specific, hand-tuned plate specs. Other books fall back to one generic plate
# per location built from the pages that mention it.
CN01_SPECS = {
    "room": {
        "refs": ["books/cn01/reader/art/p02.jpg", "books/cn01/reader/art/p12.jpg"],
        "chars": ["huahua", "xiaoman"],
        "prompt": ("16:9横构图，儿童绘本水彩插画。严格沿用参考图1、参考图2的手绘画风：水彩+彩铅勾线，"
                   "纸纹，柔和晕染，暖色调。画面：下雨天，温馨的儿童房，浅色木地板，窗户上有雨滴。"
                   "扎两个小丸子头、戴红色小花发卡、穿粉色上衣的花花，梳小马尾、戴蘑菇发夹、"
                   "穿橙色背带裤的小满，两人坐在地板上，中间是一座用彩色积木搭成的城堡，地上散落积木。"
                   "两人正开心地搭积木。中景，低机位，柔和自然光。画面无文字、无水印、无边框、无页码。"
                   "不要3D，不要写实照片，不要动漫赛璐璐。"),
    },
    "meadow": {
        "refs": ["books/cn01/reader/art/p07.jpg", "books/cn01/reader/art/p12.jpg"],
        "chars": ["xiaoman"],
        "prompt": ("16:9横构图，儿童绘本水彩插画。严格沿用参考图1、参考图2的手绘画风：水彩+彩铅勾线，"
                   "纸纹，柔和晕染，春天嫩绿。画面：春天的草地，草坡柔软，点缀小黄花和蒲公英，"
                   "梳小马尾、戴蘑菇发夹、穿橙色背带裤的小满蹲在草地上，好奇地看着叶片上的一只红黑小瓢虫。"
                   "远景是淡蓝天空和白云，光线温暖。中景，低机位，柔和自然光。画面无文字、无水印、无边框、无页码。"
                   "不要3D，不要写实照片，不要动漫赛璐璐。"),
    },
    "birthday": {
        "refs": ["books/cn01/reader/art/p08.jpg", "books/cn01/reader/art/p09.jpg",
                 "books/cn01/reader/art/p11.jpg"],
        "chars": ["huahua"],
        "prompt": ("16:9横构图，儿童绘本水彩插画。严格沿用参考图1、参考图2、参考图3的手绘画风：水彩+彩铅勾线，"
                   "纸纹，柔和晕染，暖色。画面：温馨的生日房间，窗边飘着彩色气球，桌上放着一个小生日蛋糕，"
                   "扎两个小丸子头、戴红色小花发卡、穿粉色上衣的花花坐在窗边，双手托腮，神情有点失落，"
                   "望着窗外。午后柔光。中景，平视。画面无文字、无水印、无边框、无页码。"
                   "不要3D，不要写实照片，不要动漫赛璐璐。"),
    },
}



EN01_SPECS = {
    "cinema_front": {
        "refs": ["books/en01/reader/art/p01.jpg"],
        "chars": ["kuan", "dad"],
        "prompt": ("16:9横构图，欧美儿童绘本手绘插画。严格沿用参考图的画风：粗黑马克笔勾线，"
                   "水彩/彩铅上色，纸纹，暖棕橙红灰蓝配色。画面：夜晚电影院门厅，墙上三张无字电影海报"
                   "（功夫、卓别林、赛车，只有图形没有可读文字），暖黄灯。红色刺猬头、蓝T恤、黄领结的 Kuan "
                   "和红卷发、戴眼镜、绿开衫的 Dad 站在海报前。中景，平视，柔和灯光。"
                   "画面无文字、无水印、无边框、无页码。不要3D，不要写实照片，不要日漫赛璐璐。"),
    },
    "lobby": {
        "refs": ["books/en01/reader/art/p10.jpg", "books/en01/reader/art/p13.jpg"],
        "chars": ["kuan", "dad"],
        "prompt": ("16:9横构图，欧美儿童绘本手绘插画。严格沿用参考图的画风：粗黑马克笔勾线，"
                   "水彩/彩铅上色，纸纹，暖色。画面：明亮电影院大堂，一台街机、糖果柜台、爆米花机、"
                   "彩色零食；Kuan 抱着浅蓝色圆头机器人玩具，Dad 在旁边微笑。中景，平视，暖黄灯。"
                   "画面无文字、无水印、无边框、无页码。不要3D，不要写实照片，不要日漫赛璐璐。"),
    },
    "auditorium": {
        "refs": ["books/en01/reader/art/p07.jpg", "books/en01/reader/art/p23.jpg"],
        "chars": ["kuan", "dad"],
        "prompt": ("16:9横构图，欧美儿童绘本手绘插画。严格沿用参考图的画风：粗黑马克笔勾线，"
                   "水彩/彩铅上色，纸纹，暗色影院。画面：黑暗的电影放映厅，深红色绒面座椅成排，"
                   "前方银幕发光；Kuan 抱着红白条纹爆米花桶坐在 Dad 旁边，Dad 戴眼镜穿绿开衫。"
                   "中景，平视，银幕冷光打在脸上。画面无文字、无水印、无边框、无页码。"
                   "不要3D，不要写实照片，不要日漫赛璐璐。"),
    },
    "home_table": {
        "refs": ["books/en01/reader/art/p26.jpg"],
        "chars": ["kuan", "dad"],
        "prompt": ("16:9横构图，欧美儿童绘本手绘插画。严格沿用参考图的画风：粗黑马克笔勾线，"
                   "水彩/彩铅上色，纸纹，暖色。画面：温暖的家里的餐桌，Dad 戴眼镜、红卷发、绿开衫，"
                   "看着报纸；Kuan 红色刺猬头、蓝T恤，手里举着一辆蓝色玩具车；桌上有晚餐和茶杯，"
                   "暖黄吊灯。中景，平视。画面无文字、无水印、无边框、无页码。"
                   "不要3D，不要写实照片，不要日漫赛璐璐。"),
    },
}


def _resize(src: Path, out: Path, w: int = 1280, h: int = 720) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
                    "-vf", f"scale={w}:{h}:flags=lanczos", str(out)], check=True)
    return out


def _crop_head(portrait: Path, out: Path) -> Path:
    img = cv2.imread(str(portrait))
    h, w = img.shape[:2]
    # portraits carry the competition watermark/footer at the bottom; keep the face area
    crop = img[0:int(h * 0.82), 0:w]
    cv2.imwrite(str(out), crop)
    return out


def gen_plate(bible: dict, spec: dict, out: Path, name: str) -> Path:
    if (out / f"plate_{name}.png").exists() and (out / f"plate_{name}_1280x720.png").exists():
        return out / f"plate_{name}.png"
    imgs = [data_url(ROOT / r) for r in spec["refs"]]
    for cid in spec.get("chars", []):
        c = next((x for x in bible["characters"] if x["id"] == cid), None)
        if c and (ROOT / (c.get("portrait") or "")).exists():
            imgs.append(data_url(ROOT / c["portrait"]))
    payload = {"model": SEEDREAM_MODEL, "prompt": spec["prompt"], "image": imgs,
               "size": "2K", "response_format": "url", "watermark": False}
    status, data = ark_post("/images/generations", payload, timeout=300)
    if status != 200:
        raise RuntimeError(f"Seedream {name} HTTP {status}: {json.dumps(data, ensure_ascii=False)[:500]}")
    item = (data.get("data") or [{}])[0]
    link = item.get("url")
    if not link:
        raise RuntimeError(f"Seedream {name} returned no url: {data}")
    raw = out / f"plate_{name}.png"
    download(link, raw, timeout=300)
    _resize(raw, out / f"plate_{name}_1280x720.png")
    ledger_append(out, {"ts": now(), "chunk": f"plate_{name}", "task_id": "",
                        "model": SEEDREAM_MODEL, "resolution": "2K", "duration_sec": 0,
                        "tokens": 0, "est_cny": PRICE_IMAGE_CNY, "note": spec["prompt"][:60]})
    return raw


def build_assets(bible: dict, out: str | Path, which: list[str] | None = None) -> dict:
    out = Path(out)
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    manifest_path = assets / "assets.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    # headshots from portraits (no model call, no watermark in the kept region)
    for c in bible.get("characters", []):
        rel = c.get("portrait")
        if not rel or not (ROOT / rel).exists():
            continue
        dest = assets / f"head_{c['id']}.png"
        if c["id"] == "huangmituan":
            # dog portrait carries a text line at the top; keep the lower 2/3
            img = cv2.imread(str(ROOT / rel)); h, w = img.shape[:2]
            cv2.imwrite(str(dest), img[int(h * 0.28):h, 0:w])
        else:
            _crop_head(ROOT / rel, dest)
        # Seedance r2v requires >=90k pixels per reference image; upscale tiny heads
        img = cv2.imread(str(dest))
        if img is not None and img.shape[0] * img.shape[1] < 120000:
            scale = (120000 / (img.shape[0] * img.shape[1])) ** 0.5 * 1.05
            img = cv2.resize(img, (max(1, int(img.shape[1] * scale)), max(1, int(img.shape[0] * scale))),
                             interpolation=cv2.INTER_LANCZOS4)
            cv2.imwrite(str(dest), img)
        manifest[c["id"]] = {"head": str(dest.relative_to(out))}

    if bible.get("book_id") == "cn01":
        specs = CN01_SPECS
    elif bible.get("book_id") == "en01":
        specs = EN01_SPECS
    else:
        specs = {}
    which = which or list(specs.keys())
    for name in which:
        spec = specs.get(name)
        if not spec:
            continue
        gen_plate(bible, spec, assets, name)
        manifest[f"plate_{name}"] = {"raw": f"assets/plate_{name}.png",
                                     "plate": f"assets/plate_{name}_1280x720.png",
                                     "prompt": spec["prompt"]}
    # Prefer clean, high-res heads from the generated plates (no watermark, same style).
    from heads import head_from_plate
    pairs = [("birthday", "huahua"), ("meadow", "xiaoman")]
    for plate_name, cid in pairs:
        raw = assets / f"plate_{plate_name}.png"
        p720 = assets / f"plate_{plate_name}_1280x720.png"
        dest = assets / f"head_{cid}.png"
        if head_from_plate(raw, p720, dest):
            manifest[cid] = {**manifest.get(cid, {}), "head": str(dest.relative_to(out)),
                             "head_source": f"plate_{plate_name}"}
    jwrite(manifest_path, manifest)
    return manifest