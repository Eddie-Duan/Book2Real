/* In-video search player for the non-interactive film.
   Plain browser JS, no framework, no build step.

   The page reads one contract: GET /api/film/<book> returns the run's
   timeline.json with ``source_w``/``source_h`` backfilled by the server.
   OCR boxes are in *source page* pixels and ``art_rect`` is where that page's
   art sits inside the frame, so every box travels
   source px -> art uv -> frame px -> element px. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const video = $("#film");
const canvas = $("#mark");
const ctx = canvas.getContext("2d");

const state = {
  book: "",
  data: null,
  segs: [],
  cur: 0,
  mark: null,          // { id, rect: [u0,v0,u1,v1], label }
  scrubbing: false,
  listening: false,
  hints: false,        // 是否显示"可点元素"提示圆圈（UI 层，不进成片）
  targets: null,       // books/<id>/click_targets.json 的内容
};

window.addEventListener("error", (e) => {
  const t = document.getElementById("toast");
  if (t) { t.textContent = "脚本错误：" + (e.message || "unknown"); t.hidden = false; }
});

/* ---------------------------------------------------------------- helpers */
async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, ms = 3000) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.hidden = true; }, ms);
}
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (t) => {
  const v = Math.max(0, Number(t) || 0);
  const m = Math.floor(v / 60);
  const s = Math.floor(v % 60);
  return m + ":" + String(s).padStart(2, "0");
};
const isNum = (v) => typeof v === "number" && isFinite(v) && v > 0;

/* ---------------------------------------------------------------- loading */
async function boot() {
  const pick = (new URLSearchParams(location.search).get("book") || "").trim();
  let books = [];
  try {
    books = (await api("/api/books")).books || [];
  } catch (_e) {
    books = [];
  }
  const sel = $("#bookSelect");
  sel.innerHTML = books.map(b => `<option value="${esc(b.id)}">${esc(b.title)}</option>`).join("");
  const known = books.some(b => b.id === pick);
  const book = known ? pick : (books[0] || {}).id || pick;
  if (!book) return dead("没有可以播放的绘本", "先用阅读页导入一本书，再来这里看成片。");
  sel.value = book;
  sel.onchange = () => {
    location.search = "?book=" + encodeURIComponent(sel.value);
  };
  await loadFilm(book);
}

async function loadFilm(book) {
  state.book = book;
  $("#filmMeta").textContent = "正在读取时间轴…";
  let data;
  try {
    data = await api("/api/film/" + encodeURIComponent(book));
  } catch (err) {
    $("#filmMeta").textContent = "没有成片";
    return dead("这本书还没有成片", (err && err.message) || "先回到绘本页生成一段影片。");
  }
  state.data = data;
  state.targets = null;
  // 官方脚本点名的那几个元素（[点击] 行）。只在播放器 UI 层画提示圆圈，不写进成片。
  api("/api/click_targets/" + encodeURIComponent(book))
    .then((t) => {
      state.targets = t && (t.items || []).length ? t : null;
      syncHintButton();
      paint();
    })
    .catch(() => { state.targets = null; syncHintButton(); });
  state.segs = (data.segments || []).filter(s => s && isNum(s.t1) && s.t1 > (s.t0 || 0));
  if (!state.segs.length) {
    $("#filmMeta").textContent = "时间轴是空的";
    return dead("时间轴是空的", "影片里没有可以跳转的片段。");
  }

  const title = data.title || book;
  $("#filmTitle").textContent = title;
  document.title = title + " · 影片里找一找";
  $("#filmMeta").textContent = [
    state.segs.length + " 段",
    data.duration ? fmt(data.duration) : "",
    data.mapping === "fallback" ? "框位为估算" : "",
  ].filter(Boolean).join(" · ");

  video.src = data.video_url || ("/api/film/" + encodeURIComponent(book) + "/video");
  video.load();

  const total = data.duration || state.segs[state.segs.length - 1].t1;
  $("#seek").max = String(total);
  $("#timeAll").textContent = fmt(total);
  $("#segLabel").textContent = segLabel(state.segs[0]);

  wire();
  if (!state.segs[0] || state.segs[0].kind !== "cover") showCoverCard(title);
  if (data.mapping === "fallback") {
    toast("这一版时间轴没有源页尺寸，框的位置按 art 尺寸估算。", 4200);
  }
}



function showCoverCard(title) {
  // UI-layer cover card: not baked into the film, so Q43 "no re-edit" is intact.
  // Hold 4.4s (2s longer than before); the button reads the title aloud.
  const d = document.createElement("div");
  d.id = "coverCard";
  d.style.cssText = [
    "position:fixed", "inset:0", "z-index:9999",
    "display:flex", "flex-direction:column", "align-items:center",
    "justify-content:center", "gap:14px",
    "background:radial-gradient(circle at 50% 35%,#fffaf0 0%,#f3e6d2 70%,#e6d3b8 100%)",
    "color:#5b4636", "font-family:inherit", "transition:opacity .5s ease",
  ].join(";");
  const h = document.createElement("div");
  h.textContent = title || "绘本影片";
  h.style.cssText = "font-size:clamp(28px,5vw,56px);font-weight:800;letter-spacing:.08em;text-align:center;padding:0 6vw";
  const s = document.createElement("div");
  s.textContent = "纸书智伴 · 绘本影片";
  s.style.cssText = "font-size:clamp(12px,1.8vw,18px);letter-spacing:.28em;opacity:.7";
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = "▶ 开始 · 朗读书名";
  b.style.cssText = [
    "margin-top:10px", "padding:10px 20px", "border:1px solid #b99a72",
    "border-radius:999px", "background:#fff7e8", "color:#5b4636",
    "font-size:clamp(14px,2vw,18px)", "cursor:pointer", "pointer-events:auto",
  ].join(";");
  d.appendChild(h); d.appendChild(s); d.appendChild(b);
  document.body.appendChild(d);
  let done = false;
  const kill = () => {
    if (done) return; done = true;
    try { speechSynthesis.cancel(); } catch (_e) {}
    d.style.opacity = "0"; setTimeout(() => d.remove(), 520);
  };
  const t0 = Date.now();
  const auto = setInterval(() => { if (Date.now() - t0 > 8000) { clearInterval(auto); kill(); } }, 120);
  b.onclick = () => {
    const u = new SpeechSynthesisUtterance(title || "绘本影片");
    u.lang = /[\u4e00-\u9fff]/.test(title || "") ? "zh-CN" : "en-US";
    u.rate = 0.85; u.pitch = 1.02;
    try { speechSynthesis.cancel(); speechSynthesis.speak(u); } catch (_e) {}
    b.textContent = "🔊 正在朗读…";
    clearInterval(auto);
    setTimeout(kill, 5200);
  };
  video.addEventListener("play", kill, { once: true });
}
function dead(title, note) {
  const box = $("#empty");
  $("#emptyTitle").textContent = title;
  $("#emptyNote").textContent = note || "";
  box.hidden = false;
}

/* ---------------------------------------------------------------- geometry */
function sourceOf(seg) {
  return {
    w: isNum(seg.source_w) ? seg.source_w : (isNum(seg.art_w) ? seg.art_w : 0),
    h: isNum(seg.source_h) ? seg.source_h : (isNum(seg.art_h) ? seg.art_h : 0),
  };
}

/** A pixel box on the source page -> [u0,v0,u1,v1] in art space. */
function normBox(seg, x0, y0, x1, y1) {
  const src = sourceOf(seg);
  const w = src.w || 1;
  const h = src.h || 1;
  return [Math.min(x0, x1) / w, Math.min(y0, y1) / h,
          Math.max(x0, x1) / w, Math.max(y0, y1) / h];
}

function spotNorm(seg, spot) {
  // curation.json stores spots as normalised [x, y, w, h] of the page, not in
  // source pixels; dividing by source_w here was the 12 px "box" bug.
  const b = (spot && spot.box) || [0, 0, 0, 0];
  const c = (n) => Math.min(1, Math.max(0, Number(n) || 0));
  const x0 = c(b[0]), y0 = c(b[1]);
  return [x0, y0, c(x0 + (b[2] || 0)), c(y0 + (b[3] || 0))];
}

function ocrNorm(seg, line) {
  const b = (line && line.box) || [0, 0, 0, 0];     // box is [x0,y0,x1,y1]
  return normBox(seg, b[0], b[1], b[2], b[3]);
}

/** art-normalised box -> frame pixels, through the segment's art_rect. */
function frameOf(seg, nb) {
  const r = seg.art_rect;
  if (!r || r.length !== 4) return null;
  const [ax, ay, aw, ah] = r;
  return [ax + nb[0] * aw, ay + nb[1] * ah, ax + nb[2] * aw, ay + nb[3] * ah];
}

/** Where the video picture actually sits inside the element (contain fit). */
function fitBox() {
  const rect = video.getBoundingClientRect();
  const vw = video.videoWidth || (state.data && state.data.width) || 1280;
  const vh = video.videoHeight || (state.data && state.data.height) || 720;
  const s = Math.min(rect.width / vw, rect.height / vh) || 0;
  return { w: rect.width, h: rect.height, s: s, vw: vw, vh: vh,
           ox: (rect.width - vw * s) / 2, oy: (rect.height - vh * s) / 2 };
}

function insideBox(u, v, nb, gap) {
  const g = gap || 0;
  return u >= nb[0] - g && u <= nb[2] + g && v >= nb[1] - g && v <= nb[3] + g;
}

/* ---------------------------------------------------------------- canvas */
function paint() {
  const fit = fitBox();
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const W = Math.max(1, Math.round(fit.w));
  const H = Math.max(1, Math.round(fit.h));
  canvas.style.width = W + "px";
  canvas.style.height = H + "px";
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  drawHints(fit);

  const mark = state.mark;
  if (!mark || !mark.rect) return;
  const seg = state.segs.find(s => s.id === mark.id);
  if (!seg) return;
  const box = frameOf(seg, mark.rect);
  if (!box) return;
  const s = fit.s;
  const x = fit.ox + box[0] * s;
  const y = fit.oy + box[1] * s;
  const w = Math.max(8, (box[2] - box[0]) * s);
  const h = Math.max(8, (box[3] - box[1]) * s);

  ctx.save();
  ctx.fillStyle = "rgba(255, 205, 96, 0.16)";
  ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = "#ffcd60";
  ctx.lineWidth = 3;
  ctx.strokeRect(x + 1.5, y + 1.5, Math.max(2, w - 3), Math.max(2, h - 3));

  const k = Math.min(20, w / 3, h / 3);
  ctx.strokeStyle = "#fff3cf";
  ctx.lineWidth = 4;
  [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]]
    .forEach(([cx, cy, dx, dy]) => {
      ctx.beginPath();
      ctx.moveTo(cx + dx * k, cy);
      ctx.lineTo(cx, cy);
      ctx.lineTo(cx, cy + dy * k);
      ctx.stroke();
    });
  ctx.restore();
}

/** 提示圆圈：只画官方脚本点名的那几个元素，画在 UI 画布上（成片保持干净）。 */
function drawHints(fit) {
  if (!state.hints || !state.targets) return;
  const seg = currentSeg();
  if (!seg) return;
  const items = (state.targets.items || []).filter(
    (it) => it && it.page === seg.id && Array.isArray(it.box) && it.box.length === 4);
  if (!items.length) return;
  const s = fit.s;
  items.forEach((it) => {
    const b = it.box;
    const nb = [b[0], b[1], b[0] + b[2], b[1] + b[3]];
    const box = frameOf(seg, nb);
    if (!box) return;
    const cx = fit.ox + ((box[0] + box[2]) / 2) * s;
    const cy = fit.oy + ((box[1] + box[3]) / 2) * s;
    const r = Math.max(18, Math.min(72, ((box[2] - box[0]) * s) / 2 + 12));
    ctx.save();
    ctx.setLineDash([7, 5]);
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(255, 205, 96, 0.95)";
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    const label = it.target || it.spot || "";
    if (label) {
      ctx.font = "600 12px system-ui, -apple-system, 'Segoe UI', sans-serif";
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
      ctx.fillRect(cx - tw / 2 - 6, cy + r + 4, tw + 12, 18);
      ctx.fillStyle = "#ffe9b0";
      ctx.fillText(label, cx - tw / 2, cy + r + 17);
    }
    ctx.restore();
  });
}

function syncHintButton() {
  const b = document.getElementById("btnHints");
  if (!b) return;
  const n = state.targets ? (state.targets.items || []).length : 0;
  b.disabled = n === 0;
  b.classList.toggle("on", !!state.hints);
  b.title = n ? ("可点元素 " + n + " 个（H 切换提示圆圈）") : "这本书没有固定可点元素";
}

function toggleHints() {
  if (!state.targets) return;
  state.hints = !state.hints;
  syncHintButton();
  paint();
}

function setMark(seg, nb, label) {
  state.mark = nb && seg ? { id: seg.id, rect: nb, label: label || "" } : null;
  canvas.classList.remove("fade");
  paint();
}


function clearMark() {
  if (!state.mark) return;
  state.mark = null;
  paint();
}

/* ---------------------------------------------------------------- panels */
function hideInfo() { $("#info").hidden = true; }
function hideDesc() { $("#desc").hidden = true; }

function showInfo(kicker, name, desc, page) {
  hideDesc();
  $("#infoKicker").textContent = kicker || "画面里的东西";
  $("#infoName").textContent = name || "画面里的这一块";
  $("#infoDesc").textContent = desc || "";
  $("#infoPage").textContent = page || "";
  $("#info").hidden = false;
}

function showDesc(kicker, body) {
  hideInfo();
  $("#descKicker").textContent = kicker || "这一帧";
  $("#descBody").textContent = body || "";
  $("#desc").hidden = false;
}

function pageName(seg) {
  if (!seg) return "";
  if (seg.kind === "cover") return "封面";
  if (seg.kind === "end") return "片尾";
  const n = seg.source_page;
  return n ? "第 " + n + " 页" : "第 " + (seg.id || "?") + " 页";
}

function segLabel(seg) {
  if (!seg) return "—";
  const len = Math.max(1, Math.round((seg.t1 || 0) - (seg.t0 || 0)));
  return pageName(seg) + " · " + len + "s";
}

function sceneText(seg) {
  const scene = (seg.scene || "").trim();
  const text = (seg.text || "").trim();
  if (scene && text) return "画面：" + scene + "\n正文：" + text;
  return scene || text || "这一帧没有存下说明。";
}

/* ---------------------------------------------------------------- playing */
function segAt(t) {
  const segs = state.segs;
  if (!segs.length) return 0;
  for (let i = 0; i < segs.length; i++) {
    if (t < segs[i].t1) return i;
  }
  return segs.length - 1;
}

function currentSeg() {
  return state.segs[state.cur] || state.segs[0] || null;
}

function seekTo(t) {
  const go = () => {
    const dur = isFinite(video.duration) && video.duration > 0
      ? video.duration
      : ((state.data && state.data.duration) || t);
    video.currentTime = Math.min(Math.max(0, t), Math.max(0, dur - 0.04));
  };
  if (video.readyState >= 1) go();
  else video.addEventListener("loadedmetadata", go, { once: true });
}

function jump(i, offset, pause) {
  const seg = state.segs[i];
  if (!seg) return;
  state.cur = i;
  if (pause !== false) video.pause();
  const t = Math.min((seg.t0 || 0) + (offset == null ? 0.35 : offset),
                     Math.max((seg.t0 || 0), (seg.t1 || 0) - 0.05));
  seekTo(t);
  $("#segLabel").textContent = segLabel(seg);
}

function step(dir) {
  const j = Math.min(state.segs.length - 1, Math.max(0, state.cur + dir));
  const wasPlaying = !video.paused;
  jump(j, 0.05, false);
  if (wasPlaying) video.play().catch(() => {});
  else video.pause();
}

/* ---------------------------------------------------------------- search */
/* "找到搭了九块积木的画面" must be reduced to 搭了九块积木 before scoring. */
const HEADS = ["帮我找找", "帮我找一下", "帮我找", "给我找", "能不能找到", "我想看看",
               "我想看", "让我看看", "找一下", "搜一下", "搜索", "定位到", "定位",
               "跳到", "翻到", "播放到", "找到", "看看", "看一下", "找"];
const TAILS = ["的画面", "的镜头", "的地方", "的时候", "那一幕", "这个画面",
               "那个画面", "画面", "镜头", "在哪里", "在哪儿", "在哪"];

function cleanQuery(raw) {
  const original = String(raw || "").toLowerCase().trim();
  let q = original.replace(/[\s\p{P}\p{S}]+/gu, "");
  let changed = true;
  while (changed && q) {
    changed = false;
    HEADS.forEach(h => {
      if (q.startsWith(h) && q.length > h.length) { q = q.slice(h.length); changed = true; }
    });
    TAILS.forEach(t => {
      if (q.endsWith(t) && q.length > t.length) { q = q.slice(0, -t.length); changed = true; }
    });
  }
  return q || original;
}

/** Chinese 2-grams plus latin words; case-insensitive, punctuation-blind. */
function tokensOf(q) {
  const out = [];
  (q.match(/[a-z][a-z0-9']+/g) || []).forEach(w => out.push(w));
  q.replace(/[^\u3400-\u9fff]+/g, " ").trim().split(/\s+/).filter(Boolean).forEach(run => {
    const chars = Array.from(run);
    if (chars.length === 1) out.push(chars[0]);
    for (let i = 0; i + 1 < chars.length; i++) out.push(chars[i] + chars[i + 1]);
  });
  return Array.from(new Set(out));
}

function coverage(tokens, text) {
  if (!tokens.length) return 0;
  const t = String(text || "").toLowerCase();
  if (!t) return 0;
  const flat = t.replace(/[\s\p{P}\p{S}]+/gu, "");
  let hit = 0;
  tokens.forEach(tok => { if (t.includes(tok) || flat.includes(tok)) hit++; });
  return hit / tokens.length;
}

const WEIGHTS = { text: 1.0, scene: 0.92, spot: 1.25, ocr: 0.78 };

function scoreSeg(seg, tokens) {
  let best = { score: 0, kind: "", obj: null };
  const consider = (kind, text, obj) => {
    const cov = coverage(tokens, text);
    if (!cov) return;
    const score = cov * WEIGHTS[kind];
    if (score > best.score) best = { score: score, kind: kind, obj: obj };
  };
  consider("text", seg.text, null);
  consider("scene", seg.scene, null);
  (seg.spots || []).forEach(s => consider("spot", (s.name || "") + " " + (s.desc || ""), s));
  (seg.ocr_lines || []).forEach(l => consider("ocr", l.text, l));
  return best;
}

function bestOcrLine(seg, tokens) {
  let best = null;
  (seg.ocr_lines || []).forEach(line => {
    const cov = coverage(tokens, line.text);
    if (cov > 0 && (!best || cov > best.cov)) best = { line: line, cov: cov };
  });
  return best ? best.line : null;
}

/* A tiny picture-book glossary, not a translator: the official samples name the
 * same objects in Chinese (recording script) and English (page art). Without it
 * a Chinese query can never hit an English page. */
const BRIDGE = [
  ["电影海报", ["film", "poster"]], ["海报", ["poster"]],
  ["机器人", ["robot"]], ["游戏机", ["arcade", "machine"]],
  ["爆米花", ["popcorn"]], ["汽车", ["car"]], ["电影", ["cinema", "film"]],
  ["生日卡片", ["birthday", "card"]], ["卡片", ["card"]],
  ["积木城堡", ["castle"]], ["城堡", ["castle"]], ["小狗", ["dog"]],
];

function bridgeTokens(q) {
  const extra = [];
  BRIDGE.forEach(([zh, words]) => { if (q.includes(zh)) extra.push(...words); });
  return extra;
}

function runFind(raw) {
  const q = String(raw || "").trim();
  if (!q) { toast("先说一句要找什么，比如「找到搭了九块积木的画面」"); return; }
  const tokens = Array.from(new Set(tokensOf(cleanQuery(q)).concat(bridgeTokens(q))));
  if (!tokens.length) { toast("这句话里没听出要找的词，再试一次？"); return; }

  let best = null;
  state.segs.forEach((seg, i) => {
    if (!seg.art_rect) return;
    const hit = scoreSeg(seg, tokens);
    if (hit.score > 0 && (!best || hit.score > best.hit.score)) best = { i: i, seg: seg, hit: hit };
  });
  if (!best) { toast("没找到「" + q + "」，换个说法再试试"); return; }

  jump(best.i, 0.35, true);

  if (best.hit.kind === "spot" && best.hit.obj) {
    const spot = best.hit.obj;
    setMark(best.seg, spotNorm(best.seg, spot), spot.name || "");
    showInfo("找到了这个", spot.name || "画面里的东西", spot.desc || "",
             "来自 " + pageName(best.seg));
    return;
  }
  const line = bestOcrLine(best.seg, tokens);
  if (line) {
    setMark(best.seg, ocrNorm(best.seg, line), line.text);
    showInfo("找到了这一行", line.text, "在影片的这一页上出现。", "来自 " + pageName(best.seg));
    return;
  }
  clearMark();
  showDesc("找到了这一帧", sceneText(best.seg) + "\n（" + pageName(best.seg) + "）");
}

function findWord(raw) {
  const q = String(raw || "").trim();
  if (!q) { toast("要查哪个字、哪个词？"); return; }
  const needle = q.toLowerCase();
  let best = null;
  state.segs.forEach((seg, i) => {
    if (!seg.art_rect) return;
    (seg.ocr_lines || []).forEach(line => {
      const text = String(line.text || "").toLowerCase();
      const at = text.indexOf(needle);
      if (at < 0) return;
      const score = (Number(line.score) || 0)
        + (at === 0 ? 0.3 : 0)
        + (text.length === needle.length ? 0.6 : 0);
      if (!best || score > best.score) best = { i: i, seg: seg, line: line, score: score };
    });
  });
  if (!best) { toast("这本书的画面文字里没有「" + q + "」"); return; }

  jump(best.i, 0.35, true);
  setMark(best.seg, ocrNorm(best.seg, best.line), best.line.text);
  const conf = Number(best.line.score);
  showInfo("查到「" + q + "」", best.line.text,
           isFinite(conf) ? "识别置信度 " + conf.toFixed(2) : "",
           "来自 " + pageName(best.seg));
}

/* ---------------------------------------------------------------- describe */
function describeFrame() {
  const seg = currentSeg();
  if (!seg) return;
  showDesc("这一帧 · " + pageName(seg), sceneText(seg));
}

/* ---------------------------------------------------------------- clicks */
video.addEventListener("click", (ev) => {
  const seg = currentSeg();
  if (!seg || !seg.art_rect) { toast("这一帧没有可以点的画面"); return; }
  const fit = fitBox();
  const rect = video.getBoundingClientRect();
  const fx = (ev.clientX - rect.left - fit.ox) / (fit.s || 1);
  const fy = (ev.clientY - rect.top - fit.oy) / (fit.s || 1);
  const [ax, ay, aw, ah] = seg.art_rect;
  const u = (fx - ax) / (aw || 1);
  const v = (fy - ay) / (ah || 1);

  const spot = (seg.spots || []).find(s => insideBox(u, v, spotNorm(seg, s), 0.035));
  if (spot) {
    video.pause();
    setMark(seg, spotNorm(seg, spot), spot.name || "");
    showInfo("画面里的东西", spot.name || "这里有一处细节", spot.desc || "",
             "来自 " + pageName(seg));
    return;
  }
  clearMark();
  showDesc("这一页在讲", sceneText(seg));
});

/* ---------------------------------------------------------------- controls */
function wire() {
  const btn = $("#btnPlay");
  btn.onclick = () => {
    if (video.paused) video.play().catch(() => toast("浏览器不允许自动播放，点一下画面再试"));
    else video.pause();
  };
  $("#btnPrev").onclick = () => step(-1);
  $("#btnNext").onclick = () => step(1);
  $("#btnHints").onclick = toggleHints;
  document.addEventListener("keydown", (e) => {
    if ((e.key === "h" || e.key === "H") && !e.metaKey && !e.ctrlKey && !e.altKey) toggleHints();
  });
  $("#btnDescribe").onclick = describeFrame;
  $("#infoClose").onclick = hideInfo;
  $("#descClose").onclick = hideDesc;

  $("#findForm").addEventListener("submit", (ev) => {
    ev.preventDefault();
    runFind($("#findInput").value);
  });
  $("#wordForm").addEventListener("submit", (ev) => {
    ev.preventDefault();
    findWord($("#wordInput").value);
  });

  const seek = $("#seek");
  seek.addEventListener("input", () => {
    state.scrubbing = true;
    $("#timeNow").textContent = fmt(seek.value);
    if (video.readyState >= 1) video.currentTime = Number(seek.value);
  });
  seek.addEventListener("change", () => { state.scrubbing = false; });

  video.addEventListener("loadedmetadata", () => {
    if (isFinite(video.duration) && video.duration > 0) {
      seek.max = String(video.duration);
      $("#timeAll").textContent = fmt(video.duration);
    }
    state.cur = segAt(video.currentTime || 0);
    $("#segLabel").textContent = segLabel(currentSeg());
    paint();
  });
  video.addEventListener("timeupdate", () => {
    const t = video.currentTime || 0;
    const i = segAt(t);
    if (i !== state.cur) {
      state.cur = i;
      const seg = currentSeg();
      $("#segLabel").textContent = segLabel(seg);
      if (state.mark && seg && state.mark.id !== seg.id) clearMark();
    }
    $("#timeNow").textContent = fmt(t);
    if (!state.scrubbing) seek.value = String(t);
  });
  video.addEventListener("play", () => {
    canvas.classList.add("fade");          // 播放时淡出，暂停时留着
    btn.textContent = "❚❚";
    btn.setAttribute("aria-label", "暂停");
  });
  video.addEventListener("pause", () => {
    canvas.classList.remove("fade");
    btn.textContent = "▶";
    btn.setAttribute("aria-label", "播放");
    paint();
  });
  video.addEventListener("seeked", paint);
  video.addEventListener("error", () => {
    if (video.src) toast("影片打不开：" + (video.error ? "code " + video.error.code : "未知错误"));
  });

  window.addEventListener("resize", paint);
  if (window.ResizeObserver) new ResizeObserver(paint).observe(video);

  setupMic();
}

/* ---------------------------------------------------------------- voice */
/* ---------------------------------------------------------------- voice Q&A */
function speakAnswer(text) {
  if (!("speechSynthesis" in window)) return;
  try {
    const u = new SpeechSynthesisUtterance(String(text || ""));
    u.lang = /[\u4e00-\u9fff]/.test(text || "") ? "zh-CN" : "en-US";
    u.rate = 0.95; u.pitch = 1.0;
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  } catch (_e) {}
}

function isQuestionText(text) {
  const s = String(text || "").trim();
  return /[?？]|为什么|谁|什么|哪里|哪儿|怎么|吗|多少|几个|How|Why|Who|What|Where|Which|Is |Are |Did |Does |Can |Do /i.test(s);
}

async function askQuestion(query) {
  const seg = currentSeg();
  const page = seg && seg.source_page ? "p" + String(seg.source_page).padStart(2, "0") : "";
  video.pause();
  showInfo("问一句", query, "正在想…", pageName(seg || {}));
  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({book_id: state.book, page, query}),
    });
    const data = await res.json();
    const answer = (data && data.answer) || "这一页我还没想出答案，换个问法再试试？";
    showInfo("问一句", query, answer, "来自 " + pageName(seg || {}));
    speakAnswer(answer);
  } catch (_e) {
    showInfo("问一句", query, "问答服务暂时没连上，先点画面或换一个问题。", pageName(seg || {}));
  }
}

function setupMic() {
  const btn = $("#btnMic");
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Rec) {
    btn.disabled = true;
    btn.title = "这个浏览器不支持语音识别（Chrome / Edge 可用），可以直接打字搜索";
    btn.setAttribute("aria-disabled", "true");
    return;
  }
  let rec = null;
  btn.onclick = () => {
    if (state.listening && rec) { rec.stop(); return; }
    try {
      rec = new Rec();
    } catch (_e) {
      btn.disabled = true;
      btn.title = "语音识别启动失败，改成打字搜索吧";
      return;
    }
    rec.lang = (state.data && state.data.language === "en") ? "en-US" : "zh-CN";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onstart = () => { state.listening = true; btn.textContent = "🎤 在听…"; };
    rec.onresult = (ev) => {
      const alt = ev.results && ev.results[0] && ev.results[0][0];
      const text = alt ? String(alt.transcript || "").trim() : "";
      if (!text) return;
      if (isQuestionText(text)) {
        askQuestion(text);
      } else {
        $("#findInput").value = text;
        runFind(text);
      }
    };
    rec.onerror = (ev) => {
      toast(ev && ev.error === "not-allowed"
        ? "麦克风没被允许，请允许后再试，或者直接打字。"
        : "没听清，再说一次？");
    };
    rec.onend = () => {
      state.listening = false;
      btn.textContent = "🎤 语音提问";
    };
    try {
      rec.start();
    } catch (_e) {
      toast("语音识别没能启动，改成打字搜索吧。");
    }
  };
}

boot();