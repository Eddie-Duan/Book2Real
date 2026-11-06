"""Build a child-facing word list for a book, one sentence of meaning per word.

The reader already has a 70-entry glossary; a 31-page book averages 2.2 explained
words per page, which is thin for a "learn the characters" product. This module
fills the gap with two sources:

    rules  — jieba segments the page text; nouns/verbs it already knows (
             `DEFAULT_GLOSSARY` style) are added with a template meaning;
    llm    — an OpenAI-compatible endpoint writes one short, kid-friendly
             sentence per word (used only when configured in .env).

Everything is cached in `reader/words/<page>.json` and merged into book.json by
`storybook.build`, so a re-run costs nothing and the reader keeps working with
no network at all.

    python -m book2real words --book books/charlie_lola            # rules only
    python -m book2real words --book books/charlie_lola --llm      # ask the LLM
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STOP = set("的了是在有个这那一不也很都也就说看吃喝好大小人家里上下来去做想要会能把被给和但因为所以还又再"
           "我们你们他们她们它们什么怎么这样那样时候一个自己不是没有可以已经正在慢慢地".split())
# jieba 的中文词性：n* 名词、v* 动词、a* 形容词
# segmentation glues a speech verb onto a name (劳拉说); those tails are not words
SPEECH_TAIL = ("说", "问", "答", "喊", "叫", "道", "笑", "想")

KEEP_POS = ("n", "v", "a", "d", "t", "s")


def _load_env() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def candidate_words(text: str, *, limit: int = 6) -> list[str]:
    """Words worth explaining on one page: content words, length >= 2."""
    try:
        import jieba.posseg as pseg

        pairs = list(pseg.cut(text))
    except Exception:                                  # noqa: BLE001
        import jieba

        pairs = [(w, "n") for w in jieba.cut(text)]
    out: list[str] = []
    for word, pos in pairs:
        w = word.strip()
        if len(w) < 2 or w in STOP or w in out:
            continue
        if not pos.startswith(KEEP_POS):
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", w):
            continue
        if len(w) >= 3 and w.endswith(SPEECH_TAIL):
            continue
        out.append(w)
        if len(out) >= limit:
            break
    return out


def rules_meaning(word: str, book: dict) -> str | None:
    """A real meaning from the dictionary or the glossary, else None.

    Never returns a placeholder: showing "本页出现的词：X" as a gloss is worse
    than showing nothing.
    """
    from .dictwords import MEANINGS

    g = book.get("glossary") or {}
    if word in g:
        return g[word]
    if word in MEANINGS:
        return MEANINGS[word]
    if word.endswith("们"):
        base = word[:-1]
        if base in MEANINGS or base in g:
            return f"指{base}这几个人。"
    return None


def llm_meanings(words: list[str], *, context: str = "", timeout: int = 60) -> dict:
    """Ask an OpenAI-compatible chat endpoint for one sentence per word."""
    import requests

    env = _load_env()
    key = env.get("LLM_API_KEY", "")
    base = env.get("LLM_API_BASE_URL", "").rstrip("/")
    model = env.get("LLM_API_MODEL", "")
    if not key or not base or not model:
        return {}
    prompt = (
        "你是给 6-9 岁孩子写绘本注音读物的编辑。"
        "对下面每个词，写一句不超过 22 个汉字的解释，用孩子能懂的话，不要学术腔，不要重复词本身。"
        "只输出 JSON：{\"词\": \"解释\"}。\n"
        + (f"上下文：{context}\n" if context else "")
        + "词语：" + "、".join(words)
    )
    try:
        r = requests.post(
            base + "/chat/completions",
            json={"model": model,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3,
                  "max_tokens": 400},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=timeout)
        if r.status_code != 200:
            return {}
        content = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return {}
        data = json.loads(m.group(0))
        return {k: str(v).strip()[:40] for k, v in data.items() if isinstance(v, str)}
    except Exception:                                  # noqa: BLE001
        return {}


def build(book_dir: str | Path, *, use_llm: bool = False, limit: int = 6,
          on_progress=None) -> dict:
    book_dir = Path(book_dir).resolve()
    reader = book_dir / "reader"
    book = json.loads((reader / "book.json").read_text(encoding="utf-8"))
    out_dir = reader / "words"
    out_dir.mkdir(parents=True, exist_ok=True)
    curated = {p["id"]: {w["w"] for w in p.get("words", [])} for p in book["pages"]}
    added_rules = added_llm = 0
    for page in book.get("pages", []):
        pid = page["id"]
        path = out_dir / f"{pid}.json"
        if path.exists():
            continue
        # curated/previous entries are merged later by storybook; here we only
        # add what the page does not cover yet, but never skip writing the file
        words = [w for w in candidate_words(page.get("text", ""), limit=limit)
                 if w not in curated.get(pid, set())]
        if not words:
            path.write_text("[]", encoding="utf-8")
            continue
        meanings = llm_meanings(words, context=page.get("text", "")) if use_llm else {}
        rows = []
        for w in words:
            m = meanings.get(w)
            if m:
                added_llm += 1
            else:
                m = rules_meaning(w, book)
                if m is None:
                    continue          # no fake gloss
                added_rules += 1
            rows.append({"w": w, "py": _pinyin(w), "mean": m,
                         "source": "llm" if w in meanings else "rule"})
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        if on_progress:
            on_progress(f"{pid}: {len(rows)} words")
    return {"pages": len(list(out_dir.glob("*.json"))),
            "rule": added_rules, "llm": added_llm}


def _pinyin(word: str) -> str:
    try:
        from pypinyin import Style, pinyin

        return " ".join(x[0] for x in pinyin(word, style=Style.TONE))
    except Exception:                                  # noqa: BLE001
        return ""
