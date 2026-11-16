"""绘本检索：BM25 + 中英文混检 + 意图路由（找画面/查文字/点击卡/播放控制）。"""
from __future__ import annotations

import difflib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from ..utils import detect_lang, normalize_for_tokens, normalize_text, truncate
from .aliases import expand_query

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[a-z0-9]+")
EN_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "am", "be", "been", "being",
    "who", "what", "which", "where", "when", "why", "how", "this", "that", "these", "those",
    "it", "its", "he", "she", "they", "we", "you", "i", "me", "my", "his", "her", "their", "our",
    "do", "does", "did", "can", "could", "would", "should", "will", "shall", "may", "might",
    "of", "to", "in", "on", "at", "for", "with", "and", "or", "but", "as", "by", "from",
    "there", "here", "now", "then", "very", "so", "not", "no", "yes", "oh", "ah",
    "show", "find", "look", "tell", "about", "please", "one", "some", "any", "all",
}


def tokenize(text: str) -> list[str]:
    norm = normalize_for_tokens(text)
    tokens: list[str] = []
    words = WORD_RE.findall(norm)
    for word in words:
        if word in EN_STOP:
            continue
        tokens.append(word)
        # 简单英文词形归并：films→film, musicals→musical, running→run
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            tokens.append(word[:-1])
        if len(word) > 4 and word.endswith("es"):
            tokens.append(word[:-2])
        if len(word) > 5 and word.endswith("ing"):
            tokens.append(word[:-3])
        if len(word) > 4 and word.endswith("ed"):
            tokens.append(word[:-2])
    cjk = "".join(CJK_RE.findall(norm))
    tokens.extend(cjk)
    tokens.extend(cjk[i : i + 2] for i in range(max(0, len(cjk) - 1)))
    return tokens


@dataclass
class Doc:
    id: str
    text: str
    page: int
    kind: str
    element: Optional[dict] = None
    norm: str = ""
    tf: Counter = field(default_factory=Counter)
    length: int = 0


class BookIndex:
    def __init__(self, book: dict) -> None:
        self.book = book
        self.docs: list[Doc] = []
        self._df: Counter = Counter()
        self._avgdl = 1.0
        self._build()
        self._elements_by_page: dict[int, list[dict]] = {}
        for element in book.get("elements", []):
            self._elements_by_page.setdefault(int(element.get("page") or 1), []).append(element)

    def _add(self, doc_id: str, text: str, page: int, kind: str, element: Optional[dict] = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        doc = Doc(doc_id, text, page, kind, element, normalize_text(text))
        doc.tf = Counter(tokenize(text))
        doc.length = max(1, sum(doc.tf.values()))
        self.docs.append(doc)
        for term in doc.tf:
            self._df[term] += 1

    def _build(self) -> None:
        for page in self.book.get("pages", []):
            no = int(page.get("no") or 1)
            story = page.get("story_text", "")
            if story:
                self._add(f"p{no}", story, no, "page")
                for idx, sent in enumerate(re.split(r"(?<=[。！？!?；;])", story)):
                    sent = sent.strip()
                    if len(sent) >= 6:
                        self._add(f"p{no}-s{idx}", sent, no, "sentence")
            teaching = page.get("teaching_text", "")
            if teaching:
                self._add(f"p{no}-t", teaching, no, "teaching")
        for idx, element in enumerate(self.book.get("elements", [])):
            name = element.get("name", "")
            info = element.get("info", "")
            self._add(f"e{idx}", f"{name}。{info}", int(element.get("page") or 1), "element", element)
        for item in self.book.get("script", {}).get("items", []):
            text = item.get("text", "")
            page = int(item.get("page") or 1)
            if item.get("click"):
                text += " " + item["click"].get("target", "")
            self._add(f"q{item.get('no')}", text, page, "script")
        if self.docs:
            self._avgdl = sum(d.length for d in self.docs) / len(self.docs)

    def page(self, no: int) -> Optional[dict]:
        for page in self.book.get("pages", []):
            if int(page.get("no") or 0) == int(no):
                return page
        return None

    def elements_at(self, page_no: int) -> list[dict]:
        return self._elements_by_page.get(int(page_no), [])

    def search(self, query: str, k: int = 6, prefer_page: Optional[int] = None, include_script: bool = False, include_elements: bool = False) -> list[dict]:
        q_tokens = tokenize(query)
        if not q_tokens or not self.docs:
            return []
        q_norm = normalize_text(query)
        n = len(self.docs)
        scores: list[tuple[float, Doc]] = []
        for doc in self.docs:
            if doc.kind == "script" and not include_script:
                continue
            if doc.kind == "element" and not include_elements:
                continue
            score = 0.0
            for term in q_tokens:
                tf = doc.tf.get(term, 0)
                if not tf:
                    continue
                df = self._df.get(term, 0)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                denom = tf + 1.5 * (1 - 0.75 + 0.75 * doc.length / max(1.0, self._avgdl))
                score += idf * tf * 2.5 / denom
            if not score:
                continue
            if q_norm and q_norm in doc.norm:
                score += 6.0 + min(4.0, len(q_norm) / max(1, len(doc.norm)) * 20)
            score *= {"page": 1.0, "sentence": 1.08, "element": 1.15, "script": 0.95, "teaching": 0.55}.get(doc.kind, 1.0)
            if doc.page == 1 and not any(w in q_norm for w in ("封面", "书名", "title", "cover")):
                score *= 0.55  # 封面不是正文场景，除非明确问封面/书名
            if prefer_page and doc.page == prefer_page:
                score = score * 1.18 + 2.5
            scores.append((score, doc))
        scores.sort(key=lambda x: x[0], reverse=True)
        out: list[dict] = []
        seen: set[str] = set()
        for score, doc in scores:
            key = doc.norm[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "id": doc.id,
                    "text": doc.text,
                    "page": doc.page,
                    "page_text": (self.page(doc.page) or {}).get("story_text", ""),
                    "kind": doc.kind,
                    "score": round(score, 4),
                    "element": doc.element,
                }
            )
            if len(out) >= k:
                break
        return out

    def find_text(self, needle: str, limit: int = 20) -> list[dict]:
        q = normalize_text(needle)
        if not q:
            return []
        story_hits: list[dict] = []
        other_hits: list[dict] = []
        for page in self.book.get("pages", []):
            no = int(page.get("no") or 1)
            for block in page.get("blocks", []):
                if q in normalize_text(block.get("text", "")):
                    hit = {
                        "page": no,
                        "text": block.get("text", ""),
                        "bbox": block.get("bbox"),
                        "kind": block.get("kind", "story"),
                        "score": 1.0,
                    }
                    (story_hits if block.get("kind") != "teaching" else other_hits).append(hit)
        hits = story_hits + other_hits
        return hits[:limit]

    def scene_for_query(self, query: str, prefer_page: Optional[int] = None) -> Optional[dict]:
        hits = self.search(query, k=3, prefer_page=prefer_page)
        if not hits:
            return None
        best = hits[0]
        if best["score"] < 0.35:
            return None
        return {"page": best["page"], "text": best["text"], "score": best["score"]}

    def script_page_for_query(self, query: str, min_score: float = 0.52) -> Optional[int]:
        """赛题 Query 多来自脚本：先按脚本原文对齐页码，比页面文本检索更准。

        脚本的逐条页码由入库 DP 给出，个别场景会偏一页；这里用“本动作页/下一动作页”
        的正文再校验一次，能修掉“歌舞片/害怕”这类相邻场景错位。
        """
        q = normalize_text(query)
        if not q:
            return None
        items = self.book.get("script", {}).get("items", [])
        best_score, best_idx = 0.0, None
        for idx, item in enumerate(items):
            if item.get("type") == "playback":
                continue
            fields = [item.get("scene_query"), item.get("text"), item.get("text_query")]
            click = item.get("click") or {}
            fields.append(click.get("target"))
            for field in fields:
                s = normalize_text(str(field or ""))
                if not s:
                    continue
                if q == s:
                    score = 1.0
                elif q in s or s in q:
                    score = 0.9
                else:
                    score = difflib.SequenceMatcher(None, q, s).ratio()
                if score > best_score:
                    best_score, best_idx = score, idx
        if best_score < min_score or best_idx is None:
            return None
        terms = [t for t in expand_query(query, self.book.get("lang", "zh")).replace("?", " ").split() if len(t) >= 2]
        candidates: list[int] = []
        for idx in (best_idx, best_idx + 1):
            if 0 <= idx < len(items):
                page_no = int(items[idx].get("page") or 1)
                if page_no not in candidates:
                    candidates.append(page_no)
        for page_no in candidates:
            page = self.page(page_no)
            text = (page or {}).get("story_text", "")
            if text and any(t.lower() in text.lower() for t in terms):
                return page_no
        return int(items[best_idx].get("page") or 1)

    def page_summaries(self, max_chars: int = 100) -> list[dict]:
        return [
            {"page": int(p.get("no") or 1), "text": truncate(p.get("story_text", ""), max_chars)}
            for p in self.book.get("pages", [])
        ]

    def asr_initial_prompt(self, limit: int = 240) -> str:
        """给本地 Whisper 的领域偏置：指令词 + 书名 + 点击对象 + 脚本关键引文。"""
        parts: list[str] = ["高亮", "查找", "播放", "暂停", "继续", "找到", "画面", str(self.book.get("title") or "")]
        for element in self.book.get("elements", []):
            name = str(element.get("name") or "").strip()
            if name and name not in parts:
                parts.append(name)
        for item in self.book.get("script", {}).get("items", []):
            for key in ("scene_query", "text_query"):
                value = str(item.get(key) or "").strip()
                if value and value not in parts:
                    parts.append(value)
        text = "，".join(parts)
        return text[:limit]


def route_intent(
    query: str,
    book: BookIndex,
    current_page: Optional[int] = None,
    current_element: Optional[dict] = None,
    video_available: bool = False,
) -> dict:
    """返回 {intent, action?, answer?, evidence?, passages?}。

    - intent=playback/find_scene/find_text/touch/qa/definition
    - action 由前端执行（seek / highlight / pause / play）
    """
    q = (query or "").strip()
    qn = normalize_text(q)
    lang = detect_lang(q, book.book.get("lang", "zh"))
    book_lang = book.book.get("lang", "zh")
    expanded = expand_query(q, book_lang)
    if not q:
        return {"intent": "qa", "passages": [], "lang": lang}

    # 播放控制
    if re.fullmatch(r"(播放|开始|继续|暂停|停一下|停止|结束播放|play|pause|resume|stop)", qn, re.I):
        action = "play"
        if re.search(r"暂停|停|pause|stop", qn, re.I):
            action = "pause"
        elif re.search(r"继续|播放|开始|resume|play", qn, re.I):
            action = "play"
        answer = {"play": "好的，继续播放。" if lang == "zh" else "Okay, playing.", "pause": "好的，暂停。" if lang == "zh" else "Okay, paused."}[action]
        return {"intent": "playback", "action": {"type": action}, "answer": answer, "lang": lang, "evidence": []}

    # 查文字 / 高亮
    if re.search(r"查找|高亮|find the text|highlight", q, re.I):
        quoted = re.search(r"[“\"「']([^”\"」']+)[”\"」']", q)
        needle = quoted.group(1) if quoted else re.sub(r"^(查找文字|查找|高亮文字|高亮|find the text|highlight)\s*", "", q, flags=re.I).strip()
        needle = re.sub(r"(这)?(几)?个字.*$", "", needle).strip()
        needle = re.sub(r"^(文字|单词)\s*", "", needle, flags=re.I).strip("“”\"' ?？。.!！")
        hits = book.find_text(needle)
        if hits:
            action = {"type": "highlight_text", "text": needle, "page": hits[0]["page"], "bbox": hits[0]["bbox"], "hits": hits[:8]}
            answer = (f"找到了“{needle}”，在第 {hits[0]['page']} 页。" if lang == "zh" else f"I found “{needle}” on page {hits[0]['page']}.")
            return {"intent": "find_text", "action": action, "answer": answer, "lang": lang, "evidence": hits[:3]}
        return {"intent": "find_text", "answer": ("绘本里没有找到这几个字。" if lang == "zh" else "I couldn't find that text in the book."), "lang": lang, "evidence": []}

    # 找画面
    if re.search(r"找到|画面|show me|find the scene|where is|look for", q, re.I) or q.startswith(("Where", "Find")):
        # “Where is 小满？”这类定位人物的问题：如果当前页就有这个人物，优先当前页
        loc = re.search(r"(?:where is|where's|在哪里|在哪儿)\s*([A-Za-z\u4e00-\u9fff ]+)", q, re.I)
        if loc and current_page:
            entity = loc.group(1).strip(" ?？.!。")
            page = book.page(current_page)
            if page and entity:
                expanded_entity = expand_query(entity, book_lang)
                names = [entity] + expanded_entity.split()
                if any(n and n in page.get("story_text", "") for n in names):
                    return {
                        "intent": "find_scene",
                        "action": {"type": "seek_page", "page": current_page, "query": entity},
                        "answer": (f"就在第 {current_page} 页。" if lang == "zh" else f"Here it is, on page {current_page}."),
                        "lang": lang,
                        "evidence": [{"page": current_page, "text": page.get("story_text", "")[:100]}],
                    }
        scene_query = re.sub(r"^(找到|Find|Show me|Look for)\s*", "", q, flags=re.I)
        scene_query = re.sub(r"(的)?画面.*$", "", scene_query).strip() or q
        # 1) 优先按脚本原文对齐（赛题 Query 即脚本动作），但要用页文本/当前页校验
        script_page = book.script_page_for_query(scene_query)
        if script_page:
            expanded_scene = expand_query(scene_query, book_lang)
            terms = [t for t in expanded_scene.replace("?", " ").split() if len(t) >= 2]
            page = book.page(script_page)
            page_text = (page or {}).get("story_text", "")
            entity_on_script_page = bool(page_text) and any(t.lower() in page_text.lower() for t in terms)
            cur_page = book.page(current_page) if current_page else None
            cur_text = (cur_page or {}).get("story_text", "")
            entity_on_current = bool(cur_text) and any(t.lower() in cur_text.lower() for t in terms)
            use_page = None
            if current_page and int(current_page) == int(script_page):
                use_page = script_page
            elif entity_on_current:
                use_page = current_page
            elif entity_on_script_page:
                use_page = script_page
            if use_page:
                return {
                    "intent": "find_scene",
                    "action": {"type": "seek_page", "page": use_page, "query": scene_query, "source": "script"},
                    "answer": (f"找到了，在第 {use_page} 页。" if lang == "zh" else f"Here it is, on page {use_page}."),
                    "lang": lang,
                    "evidence": [{"page": use_page, "text": scene_query}],
                }
        expanded_scene = expand_query(scene_query, book_lang)
        # 当前页就是脚本预期的场景页时，只要页内含场景实体就直接命中，避免被别的页同名物体吸走
        if current_page:
            page = book.page(current_page)
            page_text = (page or {}).get("story_text", "")
            terms = [t for t in expanded_scene.replace("?", " ").split() if len(t) >= 2]
            if page_text and any(t.lower() in page_text.lower() for t in terms):
                return {
                    "intent": "find_scene",
                    "action": {"type": "seek_page", "page": current_page, "query": scene_query},
                    "answer": (f"找到了，就在第 {current_page} 页。" if lang == "zh" else f"Here it is, on page {current_page}."),
                    "lang": lang,
                    "evidence": [{"page": current_page, "text": page_text[:100]}],
                }
        scene = book.scene_for_query(expanded_scene, prefer_page=current_page)
        if scene:
            action = {"type": "seek_page", "page": scene["page"], "query": scene_query}
            answer = (f"找到了，在第 {scene['page']} 页。" if lang == "zh" else f"Here it is, on page {scene['page']}.")
            return {"intent": "find_scene", "action": action, "answer": answer, "lang": lang, "evidence": [scene]}
        return {"intent": "find_scene", "answer": ("没有找到这个画面。" if lang == "zh" else "I couldn't find that scene."), "lang": lang, "evidence": []}

    # 点击元素卡
    if current_element and re.search(r"这是什么|这是谁|他在做什么|她在做什么|what is this|who is this|what is (he|she) doing", q, re.I):
        info = current_element.get("info") or current_element.get("name") or ""
        answer = info if lang == "zh" else f"This is {current_element.get('name', '')}. {info}"
        return {"intent": "touch", "answer": truncate(answer, 120), "lang": lang, "evidence": [{"element": current_element}], "action": {"type": "element_card", "element": current_element}}

    # 词语解释
    if re.search(r"单词|什么意思|meaning|what does .* mean", q, re.I):
        word = re.sub(r"单词|什么意思|meaning|what does|mean|的|“|”|\"|'|\?", "", q, flags=re.I).strip()
        hits = book.search(expand_query(word or q, book_lang), k=3, prefer_page=current_page)
        return {"intent": "definition", "word": word, "passages": hits, "lang": lang, "evidence": hits[:2]}

    hits = book.search(expanded, k=6, prefer_page=current_page)
    if current_page:
        page = book.page(current_page)
        page_text = (page or {}).get("story_text", "")
        if page_text and not any(int(h.get("page") or 0) == int(current_page) for h in hits):
            hits.append(
                {
                    "id": f"current-{current_page}",
                    "text": page_text,
                    "page": current_page,
                    "page_text": page_text,
                    "kind": "current_page",
                    "score": 0.0,
                    "element": None,
                }
            )
    return {"intent": "qa", "passages": hits, "lang": lang, "evidence": hits[:3]}
