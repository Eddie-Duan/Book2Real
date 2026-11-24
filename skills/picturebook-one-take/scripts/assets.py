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

def _book_dir_of(bible: dict) -> Path | None:
    for rel in ((bible.get("source") or {}).get("book_json") or "", ):
        if rel:
            cand = ROOT / rel
            if cand.is_file():
                return cand.parent.parent
    bid = bible.get("book_id")
    cand = ROOT / "books" / str(bid) if bid else None
    return cand if cand and cand.is_dir() else None


def plate_specs(bible: dict) -> dict:
    """plate 规格：优先 books/<id>/one_take/plates.json；否则按 locations 合成通用 plate。

    以前 plate 规格按书名写死在代码里，只有那两本能出图；现在缺配置也能跑
    （用该场景首现页的 reader 原图当参考 + 通用风格提示词），换新书不用改代码。
    """
    book_dir = _book_dir_of(bible)
    if book_dir is not None:
        p = book_dir / "one_take" / "plates.json"
        if p.is_file():
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                plates = doc.get("plates") or {}
                if plates:
                    return plates
            except Exception:
                pass

    style = (bible.get("style") or {}).get("prompt") or "儿童绘本手绘水彩风格，柔和配色，纸纹"
    negative = (bible.get("style") or {}).get("negative") or "文字, 水印, logo, 边框"
    pages = bible.get("pages") or []
    specs: dict = {}
    for loc in bible.get("locations") or []:
        lid = loc.get("id")
        if not lid:
            continue
        first = next((p for p in pages if p.get("id") in (loc.get("pages") or [])), None)
        refs = []
        if first and first.get("id"):
            refs = [f"books/{bible.get('book_id')}/reader/art/{first['id']}.jpg"]
        specs[lid] = {
            "refs": refs,
            "chars": [c["id"] for c in (bible.get("characters") or []) if c.get("id")],
            "prompt": (f"16:9横构图。{style} 画面：{loc.get('name') or lid}，"
                       f"保持参考图的手绘质感与配色；中景，平视，柔和自然光。"
                       f"画面无文字、无水印、无边框、无页码。不要3D，不要写实照片。负向：{negative}"),
        }
    return specs


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

    specs = plate_specs(bible)
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