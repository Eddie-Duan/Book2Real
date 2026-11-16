/* Page Q&A for the picture-book reader.
 *
 * Zero build, zero dependencies, browser-native ESM. Everything here degrades:
 * the server may be down, it may 404, return HTML, or die mid-request. In every
 * one of those cases the panel says "这一页的问答服务没打开" and the reader keeps
 * working — no white page, no uncaught rejection.
 *
 * The request/response shape is the backend's (`POST /api/ask`:
 * `{book_id, text, page, session_id, element?}` -> `answer/action/evidence/...`).
 * `citations/source/socratic` come from backend/app/compat.py so the panel keeps
 * its quote highlighting; `evidence` is also understood directly here.
 *
 * Exports (docs/INTEGRATION_CONTRACT.md §3):
 *   ask(req) -> Promise<resp>
 *   renderAnswer(el, resp)
 *   openPanel({book,page,mode,entity,box,query})
 * plus the small helpers reader.js needs to close it again.
 */

const TIMEOUT_MS = 20000;
const HISTORY_MAX = 6;
const DEAD = "这一页的问答服务没打开";

let history = [];              // [{q,a}] last 6 rounds, same page only
let ctx = null;                // {book,page,mode}
let inflight = 0;

/* --------------------------------------------------------------- response */
function degraded(page, why) {
  return {
    answer: DEAD,
    citations: [],
    source: "local",
    fallback: "qa_unavailable",
    fallback_detail: why || "",
    socratic: { level: 0, directive: "none", line: "" },
    latency_ms: 0,
    page: page || "",
  };
}

function normalize(j, page) {
  const out = {
    answer: typeof j.answer === "string" && j.answer.trim() ? j.answer.trim() : DEAD,
    citations: Array.isArray(j.citations) && j.citations.length
      ? j.citations
      : (Array.isArray(j.evidence) ? j.evidence.map(e => ({
          page: e && e.page ? "p" + String(e.page).padStart(2, "0") : page || "",
          quote: String((e && (e.text || e.quote)) || ""),
          kind: "text",
        })) : []),
    source: j.source === "llm" ? "llm" : (j.source === "rule" ? "rule" : "rule"),
    fallback: j.fallback == null ? null : String(j.fallback),
    fallback_detail: "",
    socratic: j.socratic && typeof j.socratic === "object"
      ? { level: Number(j.socratic.level) || 0,
          directive: String(j.socratic.directive || "none"),
          line: String(j.socratic.line || "") }
      : { level: 0, directive: "none", line: "" },
    latency_ms: Number(j.latency_ms) || 0,
    page: String(j.page || page || ""),
  };
  if (out.answer.length > 220) out.answer = out.answer.slice(0, 220) + "…";
  out.citations = out.citations
    .filter(c => c && typeof c === "object")
    .slice(0, 4)
    .map(c => ({
      page: String(c.page || out.page || ""),
      quote: String(c.quote || "").slice(0, 80),
      kind: ["text", "scene", "desc", "spot"].includes(c.kind) ? c.kind : "text",
    }));
  return out;
}

export async function ask(req = {}) {
  // backend 契约：page 是整数（1 起），会话历史由后端 SQLite 维护
  const num = (() => {
    const m = /^\s*p?\s*0*(\d+)\s*$/i.exec(String(req.page || ""));
    return m ? Number(m[1]) : null;
  })();
  const pageOf = (n) => (n ? "p" + String(n).padStart(2, "0") : "");

  const payload = {
    book_id: String(req.book || ""),
    page: num,
    text: String(req.query || ""),
    session_id: "reader-" + String(req.book || "book"),
  };
  if (req.entity && typeof req.entity === "object") {
    payload.element = {
      name: String(req.entity.name || ""),
      info: String(req.entity.desc || req.entity.info || ""),
    };
    if (num) payload.element.page = num;
    if (Array.isArray(req.box) && req.box.length === 4) {
      const [x, y, w, h] = req.box.map(n => Math.max(0, Math.min(1, Number(n) || 0)));
      payload.element.bbox = [x, y, x + w, y + h];
    }
  }

  if (!payload.book_id) return degraded(pageOf(num), "书名还没加载完");

  let timer = null;
  let ctrl = null;
  try {
    if (typeof AbortController !== "undefined") {
      ctrl = new AbortController();
      timer = setTimeout(() => { try { ctrl.abort(); } catch (_) {} }, TIMEOUT_MS);
    }
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
      signal: ctrl ? ctrl.signal : undefined,
    });
    if (!res.ok) return degraded(pageOf(num), `HTTP ${res.status}`);
    const ctype = (res.headers.get("content-type") || "").toLowerCase();
    if (!ctype.includes("json")) return degraded(pageOf(num), "返回的不是 JSON");
    let data = null;
    try { data = await res.json(); } catch (_) { return degraded(pageOf(num), "JSON 解析失败"); }
    if (!data || typeof data !== "object") return degraded(pageOf(num), "返回内容为空");
    if (typeof data.answer !== "string") return degraded(pageOf(num), "返回里没有 answer 字段");
    return normalize(data, pageOf(num));
  } catch (e) {
    const why = e && e.name === "AbortError" ? "请求超时" : (e && e.message) || "网络错误";
    return degraded(pageOf(num), why);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/* --------------------------------------------------------------- rendering */
const esc = (s) => String(s == null ? "" : s)
  .replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const SOURCE_LABEL = {
  "": "规则作答",
  no_llm_key: "无 LLM key · 规则作答",
  llm_error: "模型超时 · 已回落规则",
  no_evidence: "这一页没有直接依据",
  qa_unavailable: "问答服务未开",
};
const MODE_KICKER = { spot: "点画问答", word: "这个字", free: "问一句" };

function clearCiteMarks() {
  if (typeof document === "undefined") return;
  document.querySelectorAll("#story .cite-hit").forEach(n => n.classList.remove("cite-hit"));
}

/* A citation quote is highlighted character by character. Every character on
 * the page is one DOM node — `<span class="ch">` inside a ruby, or a
 * `<span class="punct">` for punctuation — so the quote is found by rebuilding
 * the page text from those nodes in document order, then marking the slice. */
function markQuote(quote) {
  if (typeof document === "undefined") return false;
  const q = String(quote || "").replace(/\s/g, "");
  if (!q) return false;
  const nodes = Array.from(document.querySelectorAll("#story .chunk .ch, #story .chunk .punct"));
  if (!nodes.length) return false;
  const text = nodes.map(n => n.textContent).join("");
  const at = text.indexOf(q);
  if (at < 0) return false;
  nodes.slice(at, at + q.length).forEach(n => n.classList.add("cite-hit"));
  setTimeout(clearCiteMarks, 2600);
  return true;
}

export function renderAnswer(el, resp) {
  if (!el) return;
  const r = resp || degraded("");
  const badge = r.fallback ? (SOURCE_LABEL[r.fallback] || r.fallback) : SOURCE_LABEL[""];
  const cites = (r.citations || []).map((c, i) =>
    `<button class="qa-cite" data-i="${i}" data-quote="${esc(c.quote)}" title="在正文里找这句话">
       <span class="qa-cite-page">${esc(c.page || "")}</span>
       <span class="qa-cite-quote">${esc(c.quote || "（本页原文）")}</span>
     </button>`).join("");
  const soc = r.socratic && r.socratic.directive && r.socratic.directive !== "none" && r.socratic.line
    ? `<p class="qa-soc">${esc(r.socratic.line)}</p>` : "";
  const dead = r.fallback === "qa_unavailable";
  el.innerHTML = `
    <div class="qa-badge ${dead ? "warn" : ""}">${esc(badge)}${r.fallback_detail ? ` · ${esc(r.fallback_detail)}` : ""}</div>
    <p class="qa-answer">${esc(r.answer)}</p>
    ${soc}
    ${cites ? `<div class="qa-cites-inline">${cites}</div>` : ""}
    ${dead ? `<p class="qa-dead">问答面板还在，页面本身不受影响：翻页、点字、朗读都照常。</p>` : ""}`;
  el.querySelectorAll(".qa-cite").forEach(btn => {
    btn.onclick = () => {
      const ok = markQuote(btn.dataset.quote);
      if (!ok) btn.classList.add("miss");
    };
  });
}

/* --------------------------------------------------------------- the panel */
function $(id) { return typeof document === "undefined" ? null : document.getElementById(id); }
function panelParts() {
  const panel = $("qaPanel"), body = $("qaBody");
  if (!panel || !body) return null;
  return {
    panel, body,
    kicker: $("qaKicker"), title: $("qaTitle"), close: $("qaClose"),
    form: $("qaForm"), input: $("qaInput"), backdrop: $("modalBackdrop"),
  };
}

function otherModalOpen() {
  return ["puppetModal", "videoModal", "importModal", "offlineModal"]
    .some(id => { const el = $(id); return el && !el.hidden; });
}

export function closePanel() {
  const p = panelParts();
  if (!p) return;
  p.panel.hidden = true;
  if (p.backdrop && !otherModalOpen()) p.backdrop.hidden = true;
  clearCiteMarks();
}

export function isOpen() {
  const p = panelParts();
  return !!(p && !p.panel.hidden);
}

function openShell(opts) {
  const p = panelParts();
  if (!p) return null;
  if (p.backdrop) p.backdrop.hidden = false;
  p.panel.hidden = false;
  if (p.kicker) p.kicker.textContent = MODE_KICKER[opts.mode] || "这一页";
  if (p.title) {
    p.title.textContent = opts.entity && opts.entity.name
      ? `关于「${opts.entity.name}」`
      : opts.mode === "free" ? "你想知道什么？" : "这一页里的小问题";
  }
  if (p.close) p.close.onclick = closePanel;
  if (p.form) {
    p.form.onsubmit = (e) => {
      e.preventDefault();
      const q = (p.input && p.input.value || "").trim();
      if (!q) { focusInput(); return; }
      if (p.input) p.input.value = "";
      runAsk({ mode: "free", query: q });
    };
  }
  return p;
}

function focusInput() {
  const p = panelParts();
  if (p && p.input && !p.panel.hidden) {
    try { p.input.focus({ preventScroll: true }); } catch (_) { try { p.input.focus(); } catch (__) {} }
  }
}

function pushHistory(q, a) {
  const question = String(q || "").trim();
  if (!question || !a || a === DEAD) return;
  history.push({ q: question, a: String(a).slice(0, 180) });
  if (history.length > HISTORY_MAX) history = history.slice(-HISTORY_MAX);
}

async function runAsk(opts) {
  const p = panelParts();
  if (!p) return null;
  const page = ctx && ctx.page;
  if (opts.query) {
    p.body.innerHTML = `<div class="qa-badge">正在想</div><p class="qa-answer qa-thinking">……</p>`;
  } else {
    p.body.innerHTML = `<div class="qa-badge">正在看这一页</div><p class="qa-answer qa-thinking">……</p>`;
  }
  const mine = ++inflight;
  const resp = await ask({
    book: ctx && ctx.book, page, mode: opts.mode,
    entity: opts.entity || null, box: opts.box || null, query: opts.query || "",
    history,
  });
  if (mine !== inflight) return resp;      // a newer question already replaced this one
  renderAnswer(p.body, resp);
  pushHistory(opts.query || (opts.entity && opts.entity.name) || "", resp.answer);
  if (resp.fallback === "qa_unavailable") {
    try { console.info("[b2r] qa unavailable:", resp.fallback_detail); } catch (_) {}
  }
  return resp;
}

/* openPanel({book,page,mode,entity,box,query}) -> panel element (or null when
 * the page has no panel markup, e.g. an old cached reader.html). */
export async function openPanel(o = {}) {
  const opts = {
    book: String(o.book || ""),
    page: String(o.page || ""),
    mode: ["spot", "word", "free"].includes(o.mode) ? o.mode : "spot",
    entity: o.entity || null,
    box: o.box || null,
    query: String(o.query || ""),
  };
  if (ctx && (ctx.book !== opts.book || ctx.page !== opts.page)) history = [];
  ctx = { book: opts.book, page: opts.page, mode: opts.mode };

  const p = openShell(opts);
  if (!p) return null;
  if (opts.mode === "free" && !opts.query) {
    p.body.innerHTML = `<p class="qa-answer">想问哪一句？打在这里，或者按下麦克风直接说。</p>`;
    focusInput();
    return p.panel;
  }
  await runAsk(opts);
  return p.panel;
}

export default { ask, renderAnswer, openPanel, closePanel, isOpen };
