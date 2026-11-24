#!/usr/bin/env python3
"""旁白 + 角色对白的声音计划（build_audio_plan.py）。

参考 MM-StoryAgent（arXiv 2503.05242）的做法：storybook 视频的音频是**多通道**的
（旁白 / 角色对白 / 音乐 / 音效），并且角色要有稳定的声音形象。
本脚本只产出**契约**（谁在第几秒说什么、用什么音色、多快多高），不做合成。

输入：scene_plan（含每场时长与分镜）+ book.json（每页正文与对白）
输出：audio_plan.json
    {
      "book_id", "lang", "total_sec",
      "voices": {"narrator": {...}, "huahua": {...}},     # 角色音量卡
      "lines": [
        {"scene_id","page_id","start_sec","end_sec","speaker","kind":"narration|dialogue",
         "text","voice","rate","pitch","mood"}
      ],
      "channels": {"narration": N, "dialogue": M, "music": "TODO", "sfx": "TODO"}
    }

音色规则（edge-tts 可用的音色，确定性映射）：
    中文旁白 zh-CN-XiaoxiaoNeural，英文旁白 en-US-AriaNeural
    中文角色在 Yunxi / Xiaoyi / Yunjian 里按角色 id 哈希分配
    角色语速 +4%，音高 +8Hz；旁白语速 -8%
    对白情绪从相邻句子的标点判断（问号→提问，叹号→兴奋，其余→平静）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ZH_CHAR_VOICES = ["zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunjianNeural"]
EN_CHAR_VOICES = ["en-US-GuyNeural", "en-US-JennyNeural", "en-GB-RyanNeural"]
QUOTE_RE = re.compile(r"[“\"]([^”\"]{1,80})[”\"]")
MIN_LINE_SEC = 1.2


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit()).lstrip("0") or "0"


def load_page_characters(book_dir: Path) -> dict:
    """characters.json 里每个角色带出场页；反过来建成 page -> [角色名]。"""
    f = Path(book_dir) / "reader" / "characters" / "characters.json"
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}
    chars = data.get("characters") if isinstance(data, dict) else data
    out: dict[str, list[str]] = {}
    for c in chars or []:
        name = c.get("name") or c.get("id")
        for pid in c.get("pages") or []:
            out.setdefault(str(pid), []).append(str(name))
    return out


def voice_card(lang: str, characters: list[str]) -> dict:
    zh = lang.startswith("zh")
    narrator = {"voice": "zh-CN-XiaoxiaoNeural" if zh else "en-US-AriaNeural",
                "rate": "-8%", "pitch": "+0Hz", "role": "narrator"}
    cards = {"narrator": narrator}
    pool = ZH_CHAR_VOICES if zh else EN_CHAR_VOICES
    # 按角色 id 排序后轮流分配，保证同书里不同角色拿到不同音色（哈希取模会撞）
    for i, cid in enumerate(sorted(set(characters) - {"narrator"})):
        cards[cid] = {"voice": pool[i % len(pool)], "rate": "+4%", "pitch": "+8Hz",
                      "role": "character"}
    return cards


def pick_speaker(window: str, page_text: str, page_chars: list[str]) -> str:
    """先看引号前 12 字里有没有名字；没有再按本页出场角色猜。"""
    for name in page_chars:
        if name and name in window:
            return name
    if len(page_chars) == 1:
        return page_chars[0]
    if page_chars:
        counts = {n: page_text.count(n) for n in page_chars}
        best = max(counts, key=counts.get)
        if counts[best] > 0:
            return best
    return "narrator"


def split_lines(text: str, character_names: list[str], page_chars: list[str] | None = None) -> list[dict]:
    """把一页正文拆成旁白与对白：引号内是对白，说话人按「前文名字 > 本页出场角色」判定。"""
    text = (text or "").strip()
    if not text:
        return []
    page_chars = page_chars or character_names
    out = []
    pos = 0
    for m in QUOTE_RE.finditer(text):
        before = text[pos:m.start()]
        if before.strip():
            out.append({"kind": "narration", "speaker": "narrator", "text": before.strip()})
        quote = m.group(1).strip()
        window = text[max(0, m.start() - 12):m.start()]
        speaker = pick_speaker(window, text, page_chars)
        out.append({"kind": "dialogue", "speaker": speaker, "text": quote})
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        out.append({"kind": "narration", "speaker": "narrator", "text": tail.strip()})
    return out or [{"kind": "narration", "speaker": "narrator", "text": text}]


def mood_of(text: str) -> str:
    if text.endswith("？") or text.endswith("?"):
        return "curious"
    if text.endswith("！") or text.endswith("!"):
        return "excited"
    return "calm"


def build(book: dict, plan: dict, book_dir: Path | None = None) -> dict:
    lang = (book.get("language") or "zh").lower()
    pages = {}
    for p in book.get("pages") or []:
        pid = p.get("id")
        if pid is not None:
            pages[pid] = p
            pages.setdefault(str(pid), p)
    by_digits = {_digits(k): v for k, v in pages.items()}
    char_names = [c.get("name") for p in (book.get("pages") or [])
                  for c in (p.get("characters") or []) if isinstance(c, dict)]
    chars = sorted({c for c in char_names if c})
    page_char_map = load_page_characters(book_dir) if book_dir else {}
    chars = sorted(set(chars) | {n for names in page_char_map.values() for n in names})
    cards = voice_card(lang, chars)

    lines = []
    for seg in plan.get("segments") or []:
        pids = seg.get("pages") or []
        dur = int(seg.get("duration_sec") or 0)
        if not pids or dur <= 0:
            continue
        def page_of(pid):
            return pages.get(pid) or by_digits.get(_digits(pid)) or {}

        weights = [max(1, len((page_of(pid).get("text") or ""))) for pid in pids]
        total_w = sum(weights)
        t = 0.0
        for pid, w in zip(pids, weights):
            page_dur = dur * w / total_w
            page_text = (page_of(pid).get("text") or "")
            page_chars = list(page_char_map.get(pid) or page_char_map.get(str(pid)) or [])
            if not page_chars:
                page_chars = [c.get("name") for c in (page_of(pid).get("characters") or [])
                              if isinstance(c, dict) and c.get("name")]
            raw = split_lines(page_text, chars, page_chars)
            if not raw:
                t += page_dur
                continue
            # 行时长按字数分配，短于 MIN_LINE_SEC 的并入下一行
            merged: list[dict] = []
            for item in raw:
                if merged and item["kind"] == merged[-1]["kind"] and len(item["text"]) < 6:
                    merged[-1]["text"] += item["text"]
                else:
                    merged.append(item)
            char_total = sum(max(1, len(i["text"])) for i in merged) or 1
            cursor = t
            for item in merged:
                share = page_dur * max(1, len(item["text"])) / char_total
                share = max(MIN_LINE_SEC, share)
                card = cards.get(item["speaker"]) or cards["narrator"]
                lines.append({
                    "scene_id": seg.get("segment_id"),
                    "page_id": pid,
                    "start_sec": round(cursor, 2),
                    "end_sec": round(min(cursor + share, t + page_dur), 2),
                    "speaker": item["speaker"],
                    "kind": item["kind"],
                    "text": item["text"],
                    "voice": card["voice"],
                    "rate": card["rate"],
                    "pitch": card["pitch"],
                    "mood": mood_of(item["text"]),
                })
                cursor += share
            t += page_dur

    return {
        "book_id": book.get("book_id"),
        "lang": lang,
        "total_sec": int(plan.get("estimated_total_sec")
                         or sum(int(s.get("duration_sec") or 0)
                                for s in (plan.get("segments") or []))),
        "voices": cards,
        "lines": lines,
        "channels": {
            "narration": sum(1 for l in lines if l["kind"] == "narration"),
            "dialogue": sum(1 for l in lines if l["kind"] == "dialogue"),
            "music": "TODO（MM-StoryAgent 用独立音乐轨，本项目暂缺）",
            "sfx": "TODO（同上）",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="scene_plan -> audio_plan.json（旁白/对白契约）")
    ap.add_argument("--book", required=True)
    ap.add_argument("--scene-plan", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    book_path = Path(a.book) / "reader" / "book.json"
    if not book_path.is_file():
        book_path = Path(a.book) / "book.json"
    book = json.loads(book_path.read_text(encoding="utf-8"))
    plan = json.loads(Path(a.scene_plan).read_text(encoding="utf-8"))

    out = build(book, plan, Path(a.book))
    if not out["lines"]:
        print("[audio] 错误：scene-plan 与 book.json 对不上（0 行）。"
              "检查 page id 是否为 p01 / page_001 两种命名，或 book.json 是否带正文。",
              file=sys.stderr)
        return 2
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[audio] {len(out['lines'])} 行 / 旁白 {out['channels']['narration']} / "
          f"对白 {out['channels']['dialogue']} / 总长 {out['total_sec']}s")
    for l in out["lines"][:6]:
        print(f"  {l['start_sec']:>5.1f}–{l['end_sec']:>5.1f}s {l['kind']:<9} "
              f"{l['speaker']:<8} {l['voice']:<26} {l['text'][:18]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
