"""Rule-first picture-book Q&A — must work with **no LLM key at all**.

Contract: docs/INTEGRATION_CONTRACT.md §2 (the only source of truth for the
request/response JSON).  Entry point:

    answer(req: dict) -> dict      # always the §2.2 shape, never raises

Design decisions worth knowing before editing:

* **Extractive, not generative.**  Every `citations[].quote` is an exact
  substring of a real book field (page `text` / `scene` / `spots[].desc` /
  `words[].mean` / a character `desc`).  The rule answer only *wraps* those
  quotes in a short spoken sentence, so it cannot invent a fact.  If nothing
  scores above `EVIDENCE_MIN` the answer says so and `fallback="no_evidence"`
  (§2.3) — this is the anti-fabrication guarantee the demo relies on.
* **Corpus comes from the reader package only** (`reader/book.json`,
  `reader/cast/entities.json`, `reader/characters/characters.json`); no extra
  index is built, no model is downloaded.
* `book.json` pages carry `pages[].characters == []` in this book, so "the
  characters on this page" is derived from `characters[].pages` instead.
* LLM mode is opt-in via `B2R_LLM_BASE` / `B2R_LLM_KEY` / `B2R_LLM_MODEL`
  (OpenAI-compatible `/chat/completions`, urllib, 20 s).  If it fails for any
  reason the rule answer is returned with `fallback="llm_error"` (the
  "single-step degradation" evidence the competition asks for).  The LLM may
  reword the answer, but citations still come from the retrieval layer, so a
  `quote` stays an exact book substring even in LLM mode.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.request
from pathlib import Path

from .socratic import decide as _socratic_decide

ROOT = Path(__file__).resolve().parents[1]
BOOKS = ROOT / "books"

MAX_ANSWER = 180          # §2.2: answer <= 180 chars
MAX_QUERY = 200           # over-long input is clipped before scoring, never 500
MAX_HISTORY = 6           # §2.1: keep the last 6 turns
MAX_BOX = 1.0
SPOT_HIT = 0.15           # §2.3: normalized xywh IoU / centre-distance threshold
EVIDENCE_MIN = 0.30       # tuned on books/charlie_lola (see tests/test_qa.py)
LLM_TIMEOUT = 20

FALLBACKS = (None, "no_llm_key", "llm_error", "no_evidence", "qa_unavailable")
CITATION_KINDS = ("text", "scene", "desc", "spot")
MODES = ("spot", "word", "free")

NO_EVIDENCE_TAIL = "要我说说这一页发生了什么吗？"

_CACHE: dict[str, tuple[int, dict]] = {}


# --------------------------------------------------------------------------- #
# loading (reader package only; no new index files)
# --------------------------------------------------------------------------- #
def _read_json(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    prev = _CACHE.get(key)
    if prev and prev[0] == st.st_mtime_ns:
        return prev[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        _CACHE[key] = (st.st_mtime_ns, data)
    return data


def _safe_book_id(raw) -> str:
    if not isinstance(raw, str):
        return ""
    bid = raw.strip()
    if not bid or len(bid) > 80:
        return ""
    if "/" in bid or "\\" in bid or ".." in bid:
        return ""
    return bid


def _load_book(raw_id):
    """-> (book_id, book_dict|None).  Falls back to the only book on disk."""
    bid = _safe_book_id(raw_id)
    if bid:
        return bid, _read_json(Path(BOOKS) / bid / "reader" / "book.json")
    try:
        cands = sorted(
            d.name for d in Path(BOOKS).iterdir()
            if (d / "reader" / "book.json").is_file()
        )
    except OSError:
        return "", None
    if len(cands) == 1:
        return cands[0], _read_json(Path(BOOKS) / cands[0] / "reader" / "book.json")
    return "", None


def _pages(book) -> list[dict]:
    pages = book.get("pages") if isinstance(book, dict) else None
    if not isinstance(pages, list):
        return []
    return [p for p in pages if isinstance(p, dict)]


def _find_page(book, page_id: str):
    pages = _pages(book)
    if not pages:
        return None
    if page_id:
        for p in pages:
            if str(p.get("id") or "") == page_id:
                return p
        m = re.fullmatch(r"p?0*(\d+)", page_id.strip(), re.I)
        if m:
            want = int(m.group(1))
            for p in pages:
                try:
                    if int(p.get("source_page") or 0) == want:
                        return p
                except (TypeError, ValueError):
                    continue
        return None
    return pages[0]


def _characters_for(book_id: str, page_id: str) -> list[dict]:
    doc = _read_json(Path(BOOKS) / book_id / "reader" / "characters" / "characters.json") or {}
    chars = doc.get("characters")
    out = []
    if isinstance(chars, list):
        for c in chars:
            if not isinstance(c, dict):
                continue
            pages = c.get("pages")
            if isinstance(pages, list) and page_id in [str(x) for x in pages]:
                out.append(c)
    return out


def _page_entities(book_id: str, page_id: str) -> tuple[list[dict], dict]:
    doc = _read_json(Path(BOOKS) / book_id / "reader" / "cast" / "entities.json") or {}
    known = doc.get("known_names") if isinstance(doc.get("known_names"), dict) else {}
    pages = doc.get("pages") if isinstance(doc.get("pages"), dict) else {}
    info = pages.get(page_id) if isinstance(pages.get(page_id), dict) else {}
    ents = info.get("entities") if isinstance(info.get("entities"), list) else []
    return [e for e in ents if isinstance(e, dict)], known


# --------------------------------------------------------------------------- #
# corpus + scoring
# --------------------------------------------------------------------------- #
_STOP_CHARS = set(
    "的了着过吗呢吧啊呀哦嘛是在有和与就都也还又很太更最可会能要想说问看请帮"
    "我你他她它们这那哪个一些什么怎样多少对从向到把被让使而或如果因所以为以"
    "之其此该各类每等上下里中前后内外去来给就才只"
)
_SENT_RE = re.compile(r"[^。！？…\n]+(?:[。！？…]+[”’\"]?)?")


def _sentences(text: str) -> list[str]:
    out = []
    for m in _SENT_RE.finditer(text or ""):
        s = m.group(0)
        if s.strip() and re.search(r"[A-Za-z0-9\u4e00-\u9fff]", s):
            out.append(s)
    return out


def _units(page: dict, page_id: str, chars: list[dict]) -> list[dict]:
    """Every citable unit: quote is an exact substring of a real field."""
    units: list[dict] = []
    for s in _sentences(page.get("text") or ""):
        units.append({"kind": "text", "quote": s, "page": page_id, "score_text": s})
    scene = (page.get("scene") or "").strip()
    if scene:
        units.append({"kind": "scene", "quote": scene, "page": page_id, "score_text": scene})
    for spot in page.get("spots") or []:
        if not isinstance(spot, dict):
            continue
        desc = str(spot.get("desc") or "").strip()
        if desc:
            units.append({"kind": "spot", "quote": desc, "page": page_id,
                          "score_text": f"{spot.get('name') or ''} {desc}"})
    for w in page.get("words") or []:
        if not isinstance(w, dict):
            continue
        mean = str(w.get("mean") or "").strip()
        if mean:
            units.append({"kind": "desc", "quote": mean, "page": page_id,
                          "score_text": f"{w.get('w') or ''} {mean}"})
    for c in chars:
        desc = str(c.get("desc") or "").strip()
        if desc:
            units.append({"kind": "desc", "quote": desc, "page": page_id,
                          "score_text": f"{c.get('name') or ''} {c.get('role') or ''} {desc}"})
    return units


def _names_on_page(page: dict, chars: list[dict], ents: list[dict], known: dict) -> list[str]:
    names: set[str] = set()
    for spot in page.get("spots") or []:
        if isinstance(spot, dict) and isinstance(spot.get("name"), str):
            names.add(spot["name"].strip())
    for c in chars:
        if isinstance(c.get("name"), str):
            names.add(c["name"].strip())
    for e in ents:
        if isinstance(e.get("name"), str):
            names.add(e["name"].strip())
    if isinstance(known, dict):
        for key in known:
            if isinstance(key, str):
                names.add(key.strip())
    return [n for n in names if len(n) >= 2]


def _run_tokens(run: list[str]) -> list[str]:
    """Tokens inside one run of consecutive non-stop characters."""
    if len(run) >= 2:
        return [run[i] + run[i + 1] for i in range(len(run) - 1)]
    return list(run)          # §task: a lone content character is still a token


def _bigrams(text: str) -> list[str]:
    """Bigrams inside runs of consecutive non-stop chars only.

    Splitting at stop characters matters: `豚鼠在哪` used to produce the
    cross-boundary pseudo-word `鼠在` (zero corpus hits, maximum idf), which
    diluted a perfect match down to 0.29 and killed a real evidence answer.
    """
    out: list[str] = []
    for chunk in re.split(r"[^\u4e00-\u9fffA-Za-z0-9]+", text or ""):
        if not chunk:
            continue
        low = chunk.lower()
        if low.isascii():
            if len(low) >= 2:
                out.append(low)
            continue
        run: list[str] = []
        for ch in chunk:
            if ch in _STOP_CHARS:
                if run:
                    out.extend(_run_tokens(run))
                    run = []
            else:
                run.append(ch)
        if run:
            out.extend(_run_tokens(run))
    return out


_ACTION_WORDS = ("做什么", "干什么", "在干嘛", "怎么", "为什么", "发生", "后来", "怎么了")
_IDENTITY_WORDS = ("是谁", "谁是", "叫什么", "什么名字", "什么样", "是谁呀")


def _scores(units: list[dict], query: str, names: list[str]) -> list[float]:
    """IDF-weighted Chinese bigram coverage; a page name in the query wins ties."""
    if not units:
        return []
    qb = list(dict.fromkeys(_bigrams(query)))
    if not qb:
        return [0.0] * len(units)
    unit_bgs = [_bigrams(u["score_text"]) for u in units]
    n = len(units)
    df = {bg: sum(1 for bgs in unit_bgs if bg in bgs) for bg in qb}
    idf = {bg: math.log(1.0 + (n + 1) / (1.0 + df[bg])) for bg in qb}
    total = sum(idf.values()) or 1.0
    hit_names = [nm for nm in names if nm in query]
    asks_action = any(w in query for w in _ACTION_WORDS)
    asks_identity = any(w in query for w in _IDENTITY_WORDS)
    out = []
    for u, bgs in zip(units, unit_bgs):
        score = sum(idf[bg] for bg in qb if bg in bgs) / total
        if hit_names and any(nm in u["score_text"] for nm in hit_names):
            score = max(score, 0.6)
            # "X 是谁" -> a spot/character gloss beats the page summary
            if asks_identity and u["kind"] in ("spot", "desc"):
                score += 0.06
        # "X 在做什么" -> the scene line describes the action better than a quote
        if asks_action and u["kind"] == "scene" and score > 0:
            score += 0.06
        out.append(min(score, 1.0))
    return out


# --------------------------------------------------------------------------- #
# geometry: normalized xywh hit test (§2.3, threshold 0.15)
# --------------------------------------------------------------------------- #
def _box(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    if w <= 0 or h <= 0:
        return None
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    return [x, y, min(w, MAX_BOX), min(h, MAX_BOX)]


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _centre_dist(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return math.hypot((ax + aw / 2) - (bx + bw / 2), (ay + ah / 2) - (by + bh / 2))


def _spot_hit(page: dict, page_id: str, box, ents: list[dict]):
    """Best candidate under the click; None when nothing is within 0.15."""
    box = _box(box)
    if box is None:
        return None
    cands: list[dict] = []
    for spot in page.get("spots") or []:
        if not isinstance(spot, dict):
            continue
        b = _box(spot.get("box"))
        if b is not None:
            cands.append({"name": str(spot.get("name") or "").strip(),
                          "desc": str(spot.get("desc") or "").strip(),
                          "kind": "spot", "box": b})
    for ent in ents:
        b = _box(ent.get("box"))
        if b is not None:
            cands.append({"name": str(ent.get("name") or "").strip(),
                          "desc": str(ent.get("desc") or "").strip(),
                          "kind": "desc", "box": b,
                          "confidence": float(ent.get("confidence") or 0.0)})
    best, best_key = None, None
    for c in cands:
        iou = _iou(box, c["box"])
        dist = _centre_dist(box, c["box"])
        if iou < SPOT_HIT and dist > SPOT_HIT:
            continue
        key = (bool(c["name"]), c["kind"] == "spot", iou, -dist, c.get("confidence", 0.0))
        if best_key is None or key > best_key:
            best, best_key = c, key
    return best


# --------------------------------------------------------------------------- #
# answer composition (rule mode)
# --------------------------------------------------------------------------- #
# pure-intent questions ("他们在干什么？", "这一页讲了什么") — D2
# --------------------------------------------------------------------------- #
# A question made only of question scaffolding ("what are they doing", "what
# does this page say") has no content word of its own, so bigram retrieval
# scores 0.0.  For those we fall back to the page's own `scene` as baseline
# evidence.  The guard that keeps the anti-fabrication red line is the same
# rule: strip the scaffolding, then demand that NOT A SINGLE content char is
# left over.  "恐龙在干什么？" keeps 恐龙, so it is NOT an intent-only question
# and still ends in no_evidence.
_INTENT_PHRASES = (
    "这一页讲了什么", "这一页讲的是什么", "这一页讲什么", "这一页说的是什么",
    "这一页", "这页", "他们在干什么", "他们在做什么", "他们在干嘛",
    "在干什么", "在做什么", "在干嘛", "干什么", "做什么", "干些什么",
    "发生什么事情", "发生了什么事", "发生了什么", "发生什么",
    "讲了什么", "讲的是什么", "讲什么", "说的是什么", "这讲的是",
    "怎么回事", "怎么了", "怎么啦", "什么样", "怎么样",
    "什么情况", "这是哪里", "这是哪儿", "这里", "他们是谁", "这是谁",
    "在哪里", "在哪儿", "在哪", "是什么意思", "是什么", "是谁", "干啥",
)
_INTENT_LEFTOVER = set(
    "干什么在嘛发生怎讲页啥呀啊呢吗吧哦了的是这那他她它们我你情况"
    "故事画面景里处东西物样看说些个一这那"
)


def _intent_only(text: str) -> bool:
    """True when the question is *only* scaffolding: no content char remains."""
    text = (text or "").strip()
    if not text:
        return False
    stripped = text
    for phrase in sorted(_INTENT_PHRASES, key=len, reverse=True):
        stripped = stripped.replace(phrase, "")
    stripped = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", stripped)
    residue = [ch for ch in stripped
               if ch not in _STOP_CHARS and ch not in _INTENT_LEFTOVER]
    return not residue


# --------------------------------------------------------------------------- #
def _cite(page_id: str, quote: str, kind: str) -> dict:
    return {"page": page_id, "quote": quote, "kind": kind}


def _fit(*parts: str) -> str:
    """Join spoken fragments, dropping the tail rather than cutting a quote."""
    out = ""
    for part in parts:
        if not part:
            continue
        candidate = f"{out} {part}".strip() if out else part.strip()
        if len(candidate) <= MAX_ANSWER:
            out = candidate
        elif not out:
            out = part.strip()[:MAX_ANSWER]
    return out[:MAX_ANSWER].strip()


def _overview(page: dict, page_id: str, units: list[dict]):
    scene = (page.get("scene") or "").strip()
    first_text = next((u["quote"] for u in units if u["kind"] == "text"), "")
    cits = []
    if scene:
        cits.append(_cite(page_id, scene, "scene"))
    if first_text:
        cits.append(_cite(page_id, first_text, "text"))
    if not cits:
        return "这一页只有图，没有文字。你想知道画面里的什么？", []
    answer = _fit(
        f"这一页的画面是：{scene}" if scene else "",
        f"书上写：「{first_text}」" if first_text else "",
    )
    return answer, cits


def _evidence_answer(page: dict, page_id: str, units: list[dict], scores: list[float], query: str, names: list[str]):
    ranked = sorted(range(len(units)), key=lambda i: scores[i], reverse=True)
    best = ranked[0]
    if scores[best] < EVIDENCE_MIN:
        return None
    picks = [best]
    for i in ranked[1:]:
        if len(picks) >= 2:
            break
        if scores[i] >= max(EVIDENCE_MIN, scores[best] * 0.55) and units[i]["quote"] != units[best]["quote"]:
            picks.append(i)
    top = units[best]
    second = units[picks[1]] if len(picks) > 1 else None
    cits = [_cite(page_id, top["quote"], top["kind"])]
    if second is not None:
        cits.append(_cite(page_id, second["quote"], second["kind"]))

    if top["kind"] == "text":
        answer = _fit(f"书上这一页写：「{top['quote']}」",
                      f"接着又说：「{second['quote']}」" if second and second["kind"] == "text" else "")
    elif top["kind"] == "scene":
        answer = _fit(f"这一页的画面是：{top['quote']}")
    elif top["kind"] == "spot":
        answer = _fit(f"画面里的这一处：{top['quote']}")
    else:
        answer = _fit(f"书里是这样介绍的：「{top['quote']}」")
    return answer, cits


def _word_answer(page: dict, page_id: str, units: list[dict], word: str):
    if not word:
        return None
    entry = None
    for w in page.get("words") or []:
        if isinstance(w, dict) and str(w.get("w") or "").strip() == word:
            entry = w
            break
    if entry is None:
        for w in page.get("words") or []:
            if isinstance(w, dict) and word in str(w.get("w") or ""):
                entry = w
                break
    sent = next((u for u in units if u["kind"] == "text" and word in u["quote"]), None)
    cits = []
    if sent is not None:
        cits.append(_cite(page_id, sent["quote"], "text"))
    mean = str((entry or {}).get("mean") or "").strip()
    if mean:
        cits.append(_cite(page_id, mean, "desc"))
    if not cits:
        return None
    answer = _fit(
        f"「{word}」这一页出现在：「{sent['quote']}」" if sent is not None else "",
        f"它的意思是：{mean}" if mean else "",
    )
    return answer, cits


def _spot_answer(page: dict, page_id: str, units: list[dict], hit):
    """-> (answer, citations, honest) where honest=True means "could not identify"."""
    name = hit.get("name") or ""
    desc = (hit.get("desc") or "").strip()
    cits = []
    if desc:
        cits.append(_cite(page_id, desc, hit.get("kind") or "desc"))
    if not desc and name:
        for u in units:
            if name in u["score_text"]:
                cits.append(_cite(page_id, u["quote"], u["kind"]))
                break
    if not name and not desc:
        # nothing identifiable here: say so instead of borrowing another entity
        scene = (page.get("scene") or "").strip()
        if scene:
            return (f"这块我没看清是谁。这一页的画面是：{scene}",
                    [_cite(page_id, scene, "scene")], True)
        return "这块我没看清是什么。你换个位置点点看？", [], True
    if name:
        answer = f"你点的是「{name}」。"
        if desc:
            answer = _fit(answer + desc)
        elif cits:
            answer = _fit(answer + f"书上这一页提到它：「{cits[0]['quote']}」")
        else:
            scene = (page.get("scene") or "").strip()
            if scene:
                answer = _fit(answer + f"这一页的画面是：{scene}")
                cits.append(_cite(page_id, scene, "scene"))
    else:
        answer = _fit(f"这一处画面里：{desc}" if desc else "")
    return answer or "这一页没有更多可说的了。", cits, False


def _no_evidence(label: str):
    label = (label or "这个").strip()[:18]
    return f"这一页没有提到「{label}」。{NO_EVIDENCE_TAIL}", []


# --------------------------------------------------------------------------- #
# LLM mode (§2.4, optional; tests exercise the fallback with a fake response)
# --------------------------------------------------------------------------- #
def _llm_config():
    from .cloud import load_env

    load_env()
    base = (os.environ.get("B2R_LLM_BASE") or os.environ.get("DEEPSEEK_BASE_URL")
            or os.environ.get("LLM_API_BASE_URL") or "").strip()
    key = (os.environ.get("B2R_LLM_KEY") or os.environ.get("DEEPSEEK_API_KEY")
           or os.environ.get("LLM_API_KEY") or "").strip()
    model = (os.environ.get("B2R_LLM_MODEL") or os.environ.get("DEEPSEEK_MODEL")
             or os.environ.get("LLM_API_MODEL") or "").strip()
    if not base:
        base = "https://api.deepseek.com"
    if not base or not key:
        return None
    if not base.rstrip("/").endswith("/chat/completions"):
        base = base.rstrip("/") + "/chat/completions"
    return {"url": base, "key": key, "model": model or "deepseek-chat"}


def _post_json(url: str, payload: dict, headers: dict, timeout: int = LLM_TIMEOUT):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 (contract)
        return json.loads(resp.read().decode("utf-8"))


def _material(page: dict, chars: list[dict], pages: list[dict], page_id: str) -> str:
    lines = [f"页码：{page_id}"]
    text = str(page.get("text") or "").strip()
    if text:
        lines.append(f"正文：{text}")
    scene = str(page.get("scene") or "").strip()
    if scene:
        lines.append(f"画面描述：{scene}")
    for spot in page.get("spots") or []:
        if isinstance(spot, dict) and (spot.get("name") or spot.get("desc")):
            lines.append(f"画面人物的说明（{spot.get('name') or '未命名'}）：{spot.get('desc') or ''}")
    for c in chars:
        lines.append(f"本页角色：{c.get('name') or ''}（{c.get('role') or ''}）{c.get('desc') or ''}")
    for w in page.get("words") or []:
        if isinstance(w, dict) and w.get("w"):
            lines.append(f"词义：{w.get('w')}＝{w.get('mean') or ''}")
    return "\n".join(lines)


def _llm_answer(cfg: dict, page: dict, page_id: str, chars: list[dict], query: str, history: list[dict]) -> str:
    material = _material(page, chars, [], page_id)
    system = (
        "你是一个中文绘本共读助手，正在陪一个孩子读绘本。硬约束：\n"
        "① 只依据下面给出的【绘本原文】回答，每个结论后面标注来源页码，例如（p03）；\n"
        "② 材料未覆盖的内容，直接说“本页未提及”，不得编造。\n"
        "用口语化的中文回答，不超过 120 字，不要输出 Markdown。"
    )
    turns = [f"读者问：{h.get('q') or ''}\n助手答：{h.get('a') or ''}" for h in history[-3:]]
    user = ("【绘本原文】\n" + material + "\n\n"
            + ("【之前的对话】\n" + "\n".join(turns) + "\n\n" if turns else "")
            + f"【读者的问题】{query or '这一页讲了什么？'}")
    payload = {"model": cfg["model"], "temperature": 0.2,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    data = _post_json(cfg["url"], payload, {"Authorization": f"Bearer {cfg['key']}"})
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("unexpected LLM payload") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty LLM answer")
    return re.sub(r"\s+", " ", content).strip()[:MAX_ANSWER]


# --------------------------------------------------------------------------- #
# request normalisation
# --------------------------------------------------------------------------- #
def _history(raw) -> list[dict]:
    out = []
    if isinstance(raw, list):
        for h in raw:
            if isinstance(h, dict):
                out.append({"q": str(h.get("q") or "")[:MAX_QUERY],
                            "a": str(h.get("a") or "")[:300]})
            elif isinstance(h, str):
                out.append({"q": h[:MAX_QUERY], "a": ""})
    return out[-MAX_HISTORY:]


def _state_from(history: list[dict]) -> dict:
    return {
        "level": min(3, len(history)),
        "wrong": 0,
        "asked_guess": any("猜" in str(h.get("a") or "") for h in history[-2:]),
        "locked": False,
    }


def _probe(socratic: dict) -> dict:
    """§2.3: a no-evidence answer must nudge with `probe`."""
    if socratic.get("directive") == "lock":
        return socratic
    out = dict(socratic)
    out["directive"] = "probe"
    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def _answer(req) -> dict:
    req = req if isinstance(req, dict) else {}
    book_id, book = _load_book(req.get("book"))

    raw_page = req.get("page")
    page_id = str(raw_page).strip()[:40] if isinstance(raw_page, (str, int)) else ""
    mode = str(req.get("mode") or "free").strip().lower()
    if mode not in MODES:
        mode = "free"
    raw_query = req.get("query")
    query = raw_query.strip()[:MAX_QUERY] if isinstance(raw_query, str) else ""
    entity = req.get("entity") if isinstance(req.get("entity"), dict) else {}
    ent_name = str(entity.get("name") or "").strip()[:40]
    ent_desc = str(entity.get("desc") or "").strip()[:160]
    box = _box(req.get("box"))
    history = _history(req.get("history"))
    socratic = _socratic_decide(query or ent_name, _state_from(history),
                                [h["q"] for h in history if h.get("q")])

    if not book:
        return {"answer": "这本书我这边还没载入。换一本书，或者告诉我你想看哪一页？",
                "citations": [], "source": "rule", "fallback": "qa_unavailable",
                "socratic": socratic, "page": page_id}
    page = _find_page(book, page_id)
    if page is None:
        return {"answer": f"这一页我还没载入（{page_id or '页码为空'}）。换一页再问我吧。",
                "citations": [], "source": "rule", "fallback": "no_evidence",
                "socratic": _probe(socratic), "page": page_id}

    pid = str(page.get("id") or page_id)
    chars = _characters_for(book_id, pid)
    ents, known = _page_entities(book_id, pid)
    units = _units(page, pid, chars)
    names = _names_on_page(page, chars, ents, known)

    if socratic.get("directive") == "lock":
        return {"answer": socratic.get("line") or "这个我不能做。我们回到绘本本身吧。",
                "citations": [], "source": "rule", "fallback": "no_evidence",
                "socratic": socratic, "page": pid}

    result = None                       # (answer, citations, honest)
    if mode == "spot" and box is not None:
        hit = _spot_hit(page, pid, box, ents)
        if hit is None:
            result = ("这块我没看清——可能是背景或者一个小细节。"
                      "你可以点画面里的人物，或者直接问我这一页发生了什么。", [], True)
        else:
            result = _spot_answer(page, pid, units, hit)
    elif mode == "spot" and (ent_name or ent_desc):
        search = f"{ent_name} {ent_desc}".strip()
        found = _evidence_answer(page, pid, units, _scores(units, search, names), search, names)
        result = (found[0], found[1], False) if found else None
    elif mode == "word":
        found = _word_answer(page, pid, units, query)
        if found is None:
            found = _evidence_answer(page, pid, units, _scores(units, query, names), query, names)
        result = (found[0], found[1], False) if found else None
    else:
        search = query or ent_name or ent_desc
        if search:
            if _intent_only(search):
                # D2: a question with no content word of its own ("他们在干什么？")
                # uses this page's own scene/text as baseline evidence.
                overview = _overview(page, pid, units)
                if overview[1]:
                    result = (overview[0], overview[1], False)
            else:
                found = _evidence_answer(page, pid, units, _scores(units, search, names), search, names)
                result = (found[0], found[1], False) if found else None
    if result is None and not query and not ent_name and not ent_desc and box is None:
        overview = _overview(page, pid, units)
        result = (overview[0], overview[1], False)
    if result is None and box is not None:
        # a spot click with no box hit and no usable query: never borrow an entity
        result = ("这块我没看清——可能是背景或者一个小细节。"
                  "你可以点画面里的人物，或者直接问我这一页发生了什么。", [], True)
    if result is None:
        no_ev = _no_evidence(query or ent_name or ent_desc)
        result = (no_ev[0], no_ev[1], True)

    answer_text, cits, honest = result
    answer_text = (answer_text or "").strip()
    if honest or not cits:
        answer_text = answer_text or _no_evidence(query or ent_name or ent_desc)[0]
        return {"answer": answer_text, "citations": cits, "source": "rule",
                "fallback": "no_evidence", "socratic": _probe(socratic), "page": pid}

    cfg = _llm_config()
    if cfg is not None:
        try:
            llm_text = _llm_answer(cfg, page, pid, chars, query or ent_name, history)
        except Exception:                      # noqa: BLE001 - degradation is the feature
            return {"answer": answer_text, "citations": cits, "source": "rule",
                    "fallback": "llm_error", "socratic": socratic, "page": pid}
        return {"answer": llm_text, "citations": cits, "source": "llm",
                "fallback": None, "socratic": socratic, "page": pid}

    return {"answer": answer_text, "citations": cits, "source": "rule",
            "fallback": "no_llm_key", "socratic": socratic, "page": pid}


def _finalize(resp) -> dict:
    """Last line of defence: §2.2 shape, always.  Never raises."""
    resp = resp if isinstance(resp, dict) else {}
    answer = resp.get("answer")
    answer = re.sub(r"\s+", " ", answer).strip() if isinstance(answer, str) else ""
    cits = []
    raw_cits = resp.get("citations")
    if isinstance(raw_cits, list):
        for c in raw_cits:
            if not isinstance(c, dict):
                continue
            quote, kind, pg = c.get("quote"), c.get("kind"), c.get("page")
            if not isinstance(quote, str) or not quote.strip() or kind not in CITATION_KINDS:
                continue
            cits.append({"page": str(pg or ""), "quote": quote, "kind": kind})
    source = resp.get("source") if resp.get("source") in ("rule", "llm") else "rule"
    fallback = resp.get("fallback")
    if fallback not in FALLBACKS:
        fallback = None
    if not cits and fallback is None:
        fallback = "no_evidence"          # never claim evidence we do not carry
    soc = resp.get("socratic") if isinstance(resp.get("socratic"), dict) else {}
    level = soc.get("level")
    try:
        level = max(0, min(3, int(level)))
    except (TypeError, ValueError):
        level = 0
    directive = soc.get("directive")
    if directive not in ("none", "probe", "clarify", "escalate", "lock"):
        directive = "probe"
    line = soc.get("line")
    line = line.strip() if isinstance(line, str) and line.strip() else "你觉得呢？书里哪一处让你这么想？"
    try:
        latency = max(0, int(resp.get("latency_ms") or 0))
    except (TypeError, ValueError):
        latency = 0
    return {
        "answer": answer[:MAX_ANSWER] or "我这边暂时没整理出答案，你换个问法再试试？",
        "citations": cits[:2],
        "source": source,
        "fallback": fallback,
        "socratic": {"level": level, "directive": directive, "line": line},
        "latency_ms": latency,
        "page": str(resp.get("page") or ""),
    }


def answer(req: dict) -> dict:
    """§2.2 answer.  Always returns, never raises — the /api/ask route relies on it."""
    started = time.perf_counter()
    try:
        resp = _answer(req)
    except Exception:                          # noqa: BLE001 - caller must get JSON
        resp = {"answer": "这个问题我这边处理不了。换一页、或者换个问法再试试？",
                "citations": [], "source": "rule", "fallback": "qa_unavailable",
                "socratic": {"level": 0, "directive": "probe",
                             "line": "我们先从画面里找线索，好吗？"},
                "page": ""}
    resp = dict(resp) if isinstance(resp, dict) else {}
    resp["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return _finalize(resp)
