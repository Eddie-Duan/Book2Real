#!/usr/bin/env python3
"""官方录制脚本 -> 固定可点元素表（books/<id>/click_targets.json）。

官方底线不是"整帧随便点"，而是脚本点名的那几个元素：
    [点击] 点击积木城堡 | 暂停并展示城堡信息 （必选）
    [高亮] 高亮"积木城堡"

本脚本把每本的 [点击]/[高亮] 行抽出来，对齐到 reader 的 spots（页面 + 归一化 box），
英文书的脚本是中文、书里 spot 是英文，所以带一张别名表；仍对不上的标 MISS 等人工补。

用法：
    python scripts/build_click_targets.py --books books --assets "纸书智伴企业预选赛绘本素材/绘本素材" \
        --script-suffix "绘本0" --out-dir books
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# 中文脚本名 -> 英文 spot 名（英文绘本的脚本仍是中文）
ALIASES = {
    "电影海报": ("film poster", "chaplin poster"),
    "机器人": ("robot",),
    "游戏机": ("arcade machine", "arcade"),
    "爆米花": ("popcorn machine", "popcorn"),
    "汽车": ("talking car", "car"),
    "蝙蝠翅膀": ("bat wing", "wings", "bat"),
    "熊猫": ("panda",),
    "骆驼": ("camel",),
    "小老鼠": ("mouse", "little mouse"),
    "章鱼": ("octopus",),
    "青蛙": ("frog",),
    "红丝带": ("red ribbon", "ribbon", "scarf"),
    "鸭子": ("duck",),
    "地球": ("earth", "globe", "planet"),
    "河流": ("river", "stream"),
    "垃圾桶": ("bin", "trash", "litter"),
    "积木城堡": ("castle", "block castle"),
    "生日卡片": ("birthday card", "card"),
    "黄米团": ("huangmituan", "dog"),
    "黄瓜": ("cucumber",),
    "辣椒": ("chili", "pepper"),
    "茄子": ("eggplant",),
    "小刺猬": ("hedgehog",),
    "兔子": ("rabbit", "bunny"),
    "大象": ("elephant",),
    "河马": ("hippo",),
    "企鹅": ("penguin",),
    "天鹅": ("swan",),
}

CLICK_RE = re.compile(r"^\[点击\]\s*点击(?P<target>[^|｜]+)[|｜](?P<action>.+?)(?P<req>（必选）|\(必选\))?\s*$")
HILITE_RE = re.compile(r"高亮[“\"']?(?P<target>[^”\"'，,。]+)[”\"']?")


def read_lines(path: Path) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*(\d+)[.、]\s*(.+?)\s*$", line)
        if m:
            out.append((int(m.group(1)), m.group(2)))
        else:
            stripped = line.strip()
            if stripped:
                out.append((out[-1][0] if out else 0, stripped))
    return out


def norm(s: str) -> str:
    return re.sub(r"[\s“”\"'，,。.、|｜]", "", (s or "").lower())


def spot_index(pages: list[dict]) -> list[dict]:
    idx = []
    for page in pages:
        for spot in page.get("spots") or []:
            if not isinstance(spot, dict):
                continue
            name = str(spot.get("name") or "").strip()
            box = spot.get("box")
            if not name or not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            idx.append({"page": str(page.get("id")), "spot": name,
                        "box": [round(float(v), 4) for v in box],
                        "desc": str(spot.get("desc") or "")})
    return idx


def match_target(target: str, idx: list[dict], aliases: dict) -> list[dict]:
    keys = [norm(target)]
    for zh, alts in aliases.items():
        if zh and (zh in target or target in zh):
            keys.extend(norm(a) for a in alts)
    keys = [k for k in keys if k]
    hits: list[dict] = []
    for row in idx:
        n = norm(row["spot"])
        for k in keys:
            if k.isascii() or n.isascii():
                # 英文必须按词边界：'car' 不能命中 'Scared girl'
                if re.search(rf"\b{re.escape(k)}\b", n) or re.search(rf"\b{re.escape(n)}\b", k):
                    hits.append(row)
                    break
            elif k == n or k in n or n in k:
                hits.append(row)
                break
    return hits


def match_quoted(text: str, pages: list[dict]) -> list[dict]:
    """高亮"某句话"——按 OCR 行/正文找那句原文，返回页与文本框。"""
    quoted = re.findall(r"[“\"']([^”\"']{2,})[”\"']", text)
    if not quoted:
        return []
    needle = norm(quoted[0])
    hits: list[dict] = []
    for page in pages:
        for line in page.get("ocr_lines") or []:
            if not isinstance(line, dict):
                continue
            if needle and needle[:12] in norm(str(line.get("text") or "")):
                hits.append({"page": str(page.get("id")), "spot": str(line.get("text") or ""),
                             "box": line.get("box"), "desc": "OCR 行"})
        body = norm(str(page.get("text") or ""))
        if needle and needle[:12] in body and not any(h["page"] == page.get("id") for h in hits):
            hits.append({"page": str(page.get("id")), "spot": quoted[0], "box": None,
                         "desc": "正文"})
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="录制脚本 -> click_targets.json")
    ap.add_argument("--books", type=Path, default=Path("books"))
    ap.add_argument("--assets", type=Path,
                    default=Path("纸书智伴企业预选赛绘本素材/绘本素材"))
    ap.add_argument("--out-dir", type=Path, default=Path("books"))
    a = ap.parse_args()

    summary = []
    for book_dir in sorted(p for p in a.books.iterdir() if (p / "reader" / "book.json").is_file()):
        bid = book_dir.name
        reader = json.loads((book_dir / "reader" / "book.json").read_text(encoding="utf-8"))
        pages = [p for p in (reader.get("pages") or []) if isinstance(p, dict)]
        idx = spot_index(pages)
        script = None
        for cand in a.assets.glob("*.txt"):
            if bid[-2:] in cand.name and bid[0] in ("c", "e"):
                # cn01 -> 中文绘本01 ; en01 -> 英文绘本01
                lang = "中文" if bid.startswith("cn") else "英文"
                if cand.name.startswith(lang) and bid[2:] in cand.name:
                    script = cand
                    break
        if script is None:
            summary.append({"book": bid, "script": None, "items": 0, "miss": 0})
            continue

        items = []
        for n, text in read_lines(script):
            m = CLICK_RE.match(text)
            kind = "click"
            target = ""
            required = "必选" in text
            if m:
                target = m.group("target").strip()
            elif "高亮" in text:
                h = HILITE_RE.search(text)
                if h:
                    kind, target = "highlight", h.group("target").strip()
            if not target:
                continue
            hits = match_target(target, idx, ALIASES)
            if kind == "highlight" and not hits:
                hits = match_quoted(text, pages)
                if hits:
                    kind = "highlight_text"
            first = hits[0] if hits else None
            items.append({
                "n": n,
                "kind": kind,
                "target": target,
                "required": required,
                "matched": bool(hits),
                "page": first["page"] if first else None,
                "spot": first["spot"] if first else None,
                "box": first["box"] if first else None,
                "pages": sorted({h["page"] for h in hits}),
                "desc": first["desc"] if first else "",
            })
        out = {
            "book_id": bid,
            "script": str(script),
            "note": "官方底线：只点这些固定元素；圆圈只画在播放器 UI 层，不进成片。",
            "needs_spots": not idx,
            "items": items,
        }
        target = a.out_dir / bid / "click_targets.json"
        target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        miss = sum(1 for it in items if not it["matched"])
        summary.append({"book": bid, "script": script.name, "items": len(items), "miss": miss})
        print(f"[{bid}] script={script.name} items={len(items)} miss={miss} -> {target}")

    (a.out_dir / "click_targets_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
