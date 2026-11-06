"""MotionDirector: page text -> a small beat sheet.

The renderer never reads the story; it reads beats. Keeping the plan in JSON is
what lets the web stage and the 16:9 film share one motion language.
Everything here is rule based and deterministic — no model call, no network —
but every number it emits is inside the human comfort range checked by Critic.
"""
from __future__ import annotations

import re

from .contracts import Beat, MotionPlan, Rig

EMOTIONS: dict[str, tuple[str, ...]] = {
    "joy": ("开心", "高兴", "笑", "喜欢", "好玩", "有趣", "快乐", "爱", "嗨", "太棒"),
    "anger": ("生气", "讨厌", "不行", "坏", "吵", "气死", "不许", "别"),
    "sad": ("难过", "伤心", "哭", "害怕", "担心", "不想", "可怜"),
    "surprise": ("咦", "哇", "奇怪", "居然", "竟然", "原来", "怎么"),
}

# (action, cue words, base weight)
CUES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("wave", ("招手", "挥手", "挥了挥", "再见", "你好", "嗨", "打招呼", "摆摆手"), 1.0),
    ("point", ("指", "瞧", "看", "望", "盯", "瞧见"), 0.8),
    ("nod", ("点头", "笑了笑", "笑", "同意", "对呀", "是的"), 0.7),
    ("shrug", ("不知道", "不晓得", "没办法", "耸耸肩", "谁知道"), 0.7),
    ("bounce", ("跳", "跑", "蹦", "冲", "扑"), 0.6),
    ("speak", ("说", "问", "喊", "叫", "答", "道", "嘟囔", "嚷嚷"), 1.0),
)

QUOTE = re.compile(r"[“\"]([^”\"]{1,40})[”\"]")

EMOTION_GAIN = {"joy": 1.15, "anger": 1.0, "sad": 0.7, "surprise": 1.1, "neutral": 0.9}


def detect_emotion(text: str) -> tuple[str, float]:
    best, hits = "neutral", 0
    for name, words in EMOTIONS.items():
        n = sum(text.count(w) for w in words)
        if n > hits:
            best, hits = name, n
    return best, min(1.0, 0.4 + 0.2 * hits)


def speaker(text: str, names: list[str]) -> str:
    """The character mentioned closest to a dialogue verb."""
    verbs = [text.find(v) for act, words, _ in CUES if act == "speak"
             for v in words if text.find(v) >= 0]
    if not verbs or not names:
        return ""
    best, best_d = "", 1 << 30
    for n in names:
        at = text.find(n)
        if at < 0:
            continue
        d = min(abs(at - v) for v in verbs)
        if d < best_d:
            best, best_d = n, d
    return best


def estimate_duration(text: str, *, cps: float = 4.2, pad: float = 1.6) -> float:
    return max(2.4, min(14.0, len(text) / cps + pad))


def plan(page_id: str, text: str, character: str, rig: Rig | None = None,
         duration: float | None = None) -> MotionPlan:
    rig = rig or Rig()
    emotion, _strength = detect_emotion(text)
    gain = EMOTION_GAIN.get(emotion, 0.9)
    total = duration or estimate_duration(text)
    side = "right"
    arms = [p.name for p in rig.parts if p.name.startswith("arm_")]
    if len(arms) == 1:
        side = "left" if arms[0].endswith("l") else "right"
    can_move = bool(arms)
    can_nod = any(p.name == "head" for p in rig.parts)

    found: list[tuple[float, str, float]] = []
    for action, words, weight in CUES:
        pos = min((text.find(w) for w in words if w in text), default=-1)
        if pos < 0:
            continue
        if action in ("wave", "point", "shrug", "bounce") and not can_move:
            continue
        if action == "nod" and not (can_move or can_nod):
            continue
        found.append((pos / max(1, len(text)), action, weight))

    found.sort(key=lambda t: t[0])
    beats: list[Beat] = [Beat(t=0.0, action="idle", dur=total, loop=True)]
    used: list[float] = []
    for frac, action, _weight in found[:4]:
        t = round(0.45 + frac * max(0.0, total - 1.8), 2)
        if any(abs(t - u) < 0.75 for u in used):
            continue
        if action == "wave":
            amp = min(25.0, max(8.0, 18.0 * gain))
            dur = min(2.6, max(1.2, 1.0 * (2 if emotion == "joy" else 1)))
            beats.append(Beat(t=t, action="wave", side=side, amp=round(amp, 1),
                              cycles=2.0, dur=dur))
        elif action in ("point", "shrug"):
            beats.append(Beat(t=t, action=action, side=side, amp=12.0, dur=1.4))
        elif action == "nod":
            beats.append(Beat(t=t, action="nod", amp=min(8.0, max(2.0, 4.0 * gain)),
                              cycles=1.5, dur=1.2))
        elif action == "bounce":
            beats.append(Beat(t=t, action="bounce", amp=min(6.0, max(2.0, 3.5 * gain)),
                              cycles=2.0, dur=1.0))
        else:
            quote = QUOTE.search(text)
            beats.append(Beat(t=t, action="speak", dur=1.6,
                              text=(quote.group(1) if quote else "")))
        used.append(t)

    beats.sort(key=lambda b: b.t)
    if not any(b.action == "speak" for b in beats):
        quote = QUOTE.search(text)                    # the page is one line of dialogue
        if quote:
            beats.append(Beat(t=round(min(total - 0.6, total * 0.25), 2),
                              action="speak", dur=1.8, text=quote.group(1)))
            beats.sort(key=lambda b: b.t)
    return MotionPlan(page=page_id, character=character, duration=round(total, 2),
                      emotion=emotion, beats=beats)


def plan_book(book: dict, cards: list[dict], rigs: dict[str, Rig],
              rig_pages: dict[str, str] | None = None) -> dict:
    """One plan per reader screen, spoken by the character the page is about."""
    names = [(c["id"], c["name"]) for c in cards if c.get("name")]
    movable = [cid for cid, rig in rigs.items() if rig.parts]
    rig_pages = rig_pages or {}
    out: dict[str, dict] = {}
    for page in book.get("pages", []):
        if page.get("kind") == "cover":
            continue
        text = page.get("text", "")
        who = speaker(text, [n for _, n in names]) or ""
        cid = next((i for i, n in names if n == who), "")
        if cid not in movable:
            on_page = [c for c, p in rig_pages.items() if p == page["id"] and c in movable]
            present = [i for i, n in names if n in text and i in movable]
            cid = (on_page or present or [cid])[0]
        if not cid and names:
            first = next(((i, n) for i, n in names if n in text), None)
            cid = first[0] if first else names[0][0]
        out[page["id"]] = plan(page["id"], text, cid, rigs.get(cid)).to_dict()
    return out
