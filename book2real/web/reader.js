/* Interactive picture-book reader. No framework, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const state = { books: [], book: null, index: 0, pinyin: true, size: 46, wordAt: [], currentWord: null };
window.addEventListener("error", (e) => {
  const t = document.getElementById("toast");
  if (t) { t.textContent = "脚本错误：" + (e.message || "unknown"); t.hidden = false; }
});

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(t._timer); t._timer = setTimeout(() => t.hidden = true, ms);
}
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ------------------------------------------------------------- books */
async function loadBooks(selectId) {
  const { books } = await api("/api/books");
  state.books = books;
  const sel = $("#bookSelect");
  sel.innerHTML = books.map(b => `<option value="${esc(b.id)}">${esc(b.title)} · ${b.pages} 页</option>`).join("");
  const want = selectId || new URLSearchParams(location.search).get("book") || localStorage.getItem("b2r.book");
  const pick = books.find(b => b.id === want) || books[0];
  if (pick) { sel.value = pick.id; await openBook(pick.id); }
}
async function openBook(id) {
  const data = await api(`/api/book?book=${encodeURIComponent(id)}`);
  state.book = data; state.index = 0;
  state.artifacts = null;
  fetch(`/books/${encodeURIComponent(id)}/reader/characters/characters.json`)
    .then(r => r.ok ? r.json() : null).then(j => {
      state.artifacts = j;
      if (state.book) render();
    }).catch(() => {});
  fetch(`/books/${encodeURIComponent(id)}/reader/cast/entities.json`)
    .then(r => r.ok ? r.json() : null).then(j => {
      state.entities = j;
      if (state.book) render();
    }).catch(() => {});
  state.layerIndex = {};
  fetch(`/books/${encodeURIComponent(id)}/reader/layers/index.json`)
    .then(r => r.ok ? r.json() : null).then(j => {
      state.layerIndex = (j && j.pages) || {};
      if (state.book) renderArt(state.book.pages[state.index]);
    }).catch(() => {});
  localStorage.setItem("b2r.book", id);
  const theme = data.theme || {};
  for (const [k, v] of Object.entries(theme)) document.documentElement.style.setProperty(`--${k}`, v);
  const qp = parseInt(new URLSearchParams(location.search).get("page") || "", 10);
  const saved = parseInt(localStorage.getItem("b2r.index." + id) || "", 10);
  state.index = Math.max(0, Math.min(data.pages.length - 1, Number.isFinite(qp) ? qp : (Number.isFinite(saved) ? saved : 0)));
  buildDots(); render();
}

/* ------------------------------------------------------------- render */
function buildDots() {
  $("#dots").innerHTML = state.book.pages.map((p, i) =>
    `<button class="dot-btn" data-i="${i}" aria-label="第 ${i + 1} 页"></button>`).join("");
  $$("#dots .dot-btn").forEach(b => b.onclick = () => go(+b.dataset.i));
}
function render() {
  const { book, index } = state;
  if (!book) return;
  const page = book.pages[index];
  const cover = page.kind === "cover";
  $("#kicker").textContent = cover ? "封面" : `第 ${page.source_page || index + 1} 页${page.parts > 1 ? ` · ${page.part}/${page.parts}` : ""}`;
  $("#coverTitle").hidden = !cover; $("#coverMeta").hidden = !cover;
  $("#storyTools").hidden = cover; $("#hint").hidden = cover;
  $("#sceneNote").hidden = true;

  if (cover) {
    $("#coverTitle").textContent = book.title;
    $("#coverMeta").innerHTML = `${esc(book.subtitle || "")}<br>${esc(book.author || "")}`;
    $("#story").innerHTML = `<div style="margin-top:10px"><button class="primary" id="startRead" style="padding:13px 24px">开始阅读 →</button></div>`;
    $("#startRead").onclick = () => go(1);
    $("#story").classList.remove("has-ruby");
  } else {
    renderStory(page);
  }

  renderArt(page);
  $("#pagecount").textContent = `${index + 1} / ${book.pages.length}`;
  $$("#dots .dot-btn").forEach((b, i) => b.classList.toggle("on", i === index));
  $("#btnPrev").disabled = index === 0;
  $("#btnNext").disabled = index === book.pages.length - 1;
  localStorage.setItem("b2r.index." + book.book_id, String(index));
  if (cover) $("#btnRead").disabled = true; else $("#btnRead").disabled = false;
  speechSynthesis.cancel();
  const probe = new URLSearchParams(location.search).get("probe");
  if (probe === "word" && !cover) setTimeout(() => {
    const r = $("ruby.r"); if (r) showWord(+r.dataset.i, r);
  }, 250);
  if (probe === "chars" && !cover) setTimeout(() => $("#btnChars").click(), 250);
  if (probe === "puppet" && !state._probeDone) setTimeout(() => {
    const c = castList().find(x => x.id === "lola");
    if (c && state.artifacts) {
      state._probeDone = true;
      openStage(c);
      setTimeout(() => $("#puppetWave").click(), 900);
    }
  }, 350);
  if (probe === "video") setTimeout(() => $("#btnVideo").click(), 250);
  if (probe === "read") setTimeout(() => $("#btnRead").click(), 400);
}

function renderStory(page) {
  const tokens = page.pinyin || [];
  state.wordAt = new Array(tokens.length).fill(null);
  (page.words || []).forEach(w => {
    let from = 0;
    while (true) {
      const at = page.text.indexOf(w.w, from);
      if (at < 0) break;
      for (let i = at; i < at + w.w.length; i++) state.wordAt[i] = w;
      from = at + 1;
    }
  });
  const punct = new Set([..."，。！？、；：…”’（）《》「」…—"]);
  const words = page.segments && page.segments.length ? page.segments : tokens.map(t => t.ch);
  let ti = 0;
  const units = words.map(seg => {
    const bits = [];
    for (let k = 0; k < seg.length; k++, ti++) {
      const t = tokens[ti] || { ch: seg[k], py: "" };
      bits.push(punct.has(t.ch)
        ? `<span class="punct">${esc(t.ch)}</span>`
        : `<ruby class="r" data-i="${ti}" tabindex="0"><span class="py">${esc(t.py || "")}</span><span class="ch">${esc(t.ch)}</span></ruby>`);
    }
    return `<span class="word">${bits.join("")}</span>`;
  });
  const story = $("#story");
  story.innerHTML = `<div class="chunk">${units.join("")}</div>`;
  story.scrollTop = 0;
  $$("ruby.r", story).forEach(r => {
    r.onclick = (e) => { e.stopPropagation(); showWord(+r.dataset.i, r); };
    r.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showWord(+r.dataset.i, r); } };
  });
}

/* curated spots win; otherwise the figures PageScout found become hotspots */
function resolveSpots(page) {
  if (page.spots && page.spots.length) return page.spots;
  const pages = (state.entities && state.entities.pages) || {};
  const ents = ((pages[page.id] || {}).entities) || [];
  const cast = (state.artifacts && state.artifacts.characters) || [];
  return ents.filter(e => e.name && e.area >= 0.04).slice(0, 3).map(e => {
    const c = cast.find(x => x.name === e.name);
    return { name: e.name, box: e.box, desc: (c && c.desc) || "", cid: c && c.id };
  });
}

function renderArt(page) {
  stopLayerIdle();
  const layers = $("#artLayers");
  if (state._animId) { cancelAnimationFrame(state._animId); state._animId = null; }
  layers.innerHTML = "";
  const base = `/books/${encodeURIComponent(state.book.book_id)}/reader/`;
  if (page.rig && page.rig.image) {
    renderRig(page, page.rig, base);
    return;
  }
  if (page.art_fg && page.art_bg) {
    layers.innerHTML = `<div class="art-img art-bg" style="background-image:url('${base}${esc(page.art_bg)}')"></div>
      <div class="art-img art-fg" style="background-image:url('${base}${esc(page.art_fg)}')"></div>`;
  } else {
    layers.innerHTML = `<div class="art-img art-one" style="background-image:url('${base}${esc(page.art)}')"></div>`;
    const lay = state.layerIndex && state.layerIndex[page.id];
    if (lay) attachLayers(lay, base);
  }
  const seed = state.index * 37 + 11;
  const motes = Array.from({ length: 9 }, (_, i) => {
    const x = (seed * (i + 3) * 17) % 92 + 3;
    const y = (seed * (i + 5) * 29) % 88 + 6;
    const d = ((seed + i * 7) % 130) / 10;
    const s = 0.7 + ((seed + i) % 9) / 10;
    return `<i style="left:${x}%;top:${y}%;animation-delay:-${d}s;transform:scale(${s})"></i>`;
  }).join("");
  layers.insertAdjacentHTML("beforeend", `<div class="motes">${motes}</div>`);
  const stage = $("#artStage");
  stage.onclick = (e) => {
    if (e.target.closest(".spot")) return;
    showScene(page);
  };
  const spots = $("#spots");
  const pageSpots = resolveSpots(page);
  spots.innerHTML = "";
  pageSpots.forEach((s, i) => {
    const btn = document.createElement("button");
    btn.className = "spot"; btn.dataset.i = i;
    btn.innerHTML = `<i></i><span>${esc(s.name)}</span>`;
    btn.onclick = (e) => { e.stopPropagation(); showSpot(s); if (s.cid) openStage(castList().find(c => c.id === s.cid)); };
    spots.appendChild(btn);
  });
  if (state.layoutLayers) setTimeout(state.layoutLayers, 0);
  state.layoutSpots = () => {
    const stage = $("#artStage"); const r = stage.getBoundingClientRect();
    const ar = (page.art_w || 4) / (page.art_h || 3);
    let w = r.width, h = w / ar;
    if (h > r.height) { h = r.height; w = h * ar; }
    const ox = (r.width - w) / 2, oy = (r.height - h) / 2;
    $$(".spot", spots).forEach((btn, i) => {
      const b = (pageSpots[i] && pageSpots[i].box) || [0, 0, .1, .1];
      btn.style.left = (ox + (b[0] + b[2] / 2) * w) + "px";
      btn.style.top = (oy + (b[1] + b[3] / 2) * h) + "px";
    });
  };
  state.layoutSpots();
  $("#sceneChip").style.display = pageSpots.length ? "none" : "flex";

  if (!stage._parallax) {
    stage.addEventListener("mousemove", (e) => {
      const r = stage.getBoundingClientRect();
      const dx = (e.clientX - r.left) / r.width - 0.5;
      const dy = (e.clientY - r.top) / r.height - 0.5;
      const bg = $(".art-bg"), fg = $(".art-fg");
      if (bg) bg.style.transform = `scale(1.07) translate(${dx * -10}px,${dy * -8}px)`;
      if (fg) fg.style.transform = `scale(1.02) translate(${dx * -22}px,${dy * -16}px)`;
      // depth layers: same gesture, amounts come from the depth map, not a constant
      $$(".art-depth").forEach(el => {
        const p = parseFloat(el.dataset.parallax || "0");
        el.style.transform = `scale(${el.dataset.zoom || 1}) translate(${dx * -p * 100}px,${dy * -p * 60}px)`;
      });
    });
    stage.addEventListener("mouseleave", () => {
      const bg = $(".art-bg"), fg = $(".art-fg");
      if (bg) bg.style.transform = "";
      if (fg) fg.style.transform = "";
      $$(".art-depth").forEach(el => { el.style.transform = `scale(${el.dataset.zoom || 1})`; });
    });
    stage._parallax = true;
  }
}

/* The near layer of a page is the characters. Give it a small, slow idle motion
   so the story page itself breathes instead of being a still: a 2-3 px lateral
   sway plus a tiny scale, both well under the parallax amount so it reads as
   "alive" and never as "the picture is shaking". */

/* Replace the flat page with depth layers. Amounts were baked at build time
   (depth map + figure mask), so this is pure CSS transforms: no per-frame JS. */
function attachLayers(lay, base) {
  const layers = $("#artLayers");
  if (!lay || !lay.layers || !lay.layers.length) return;
  // A page with a baked loop (cut parts swinging about a pivot) plays that loop:
  // the characters on the page itself move, not just the camera.
  if (lay.motion_gif) {
    layers.innerHTML = `<img class="art-loop" alt=""
      style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain"
      src="${base}${esc(lay.motion_gif)}">`;
    state.layerInfo = lay;
    return;
  }
  // The flat page is drawn "contain" inside the stage (letterboxed). Layer boxes
  // must use the same fitted rectangle, otherwise the near layer is redrawn at a
  // different scale and the page looks ghosted.
  layers.innerHTML = lay.layers.map(l => `
    <div class="art-img art-depth" data-parallax="${l.parallax || 0}" data-zoom="${l.zoom || 1}"
         style="inset:auto;background-image:url('${base}${esc(l.image)}');z-index:${l.z};
                transform:scale(${l.zoom || 1})"></div>`).join("");
  const stage = $("#artStage");
  const place = () => {
    const r = stage.getBoundingClientRect();
    const ar = (lay.art_w || 4) / (lay.art_h || 3);
    let w = r.width, h = w / ar;
    if (h > r.height) { h = r.height; w = h * ar; }
    const left = (r.width - w) / 2, top = (r.height - h) / 2;
    $$(".art-depth", layers).forEach(el => {
      Object.assign(el.style, { left: left + "px", top: top + "px", width: w + "px", height: h + "px" });
    });
  };
  state.layoutLayers = place;
  place();
  state.layerInfo = lay;
  startLayerIdle();
}

let layerIdleAnims = [];
function stopLayerIdle() {
  layerIdleAnims.forEach(a => { try { a.cancel(); } catch (_) {} });
  layerIdleAnims = [];
}
function startLayerIdle() {
  stopLayerIdle();
  const near = $$(".art-depth").filter(el => Number(el.dataset.z || 0) > 0);
  const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) return;
  near.forEach(el => {
    const zoom = Number(el.dataset.zoom || 1);
    try {
      layerIdleAnims.push(el.animate(
        [{ transform: `scale(${zoom}) translate(-1.6px, 0.8px)` },
         { transform: `scale(${zoom * 1.008}) translate(1.6px, -1.2px)` },
         { transform: `scale(${zoom}) translate(-1.6px, 0.8px)` }],
        { duration: 7200, iterations: Infinity, easing: "ease-in-out" }));
    } catch (_) {}
  });
}

/* ------------------------------------------------------------- 2.5D rig
   The cutout is drawn as a triangle mesh. A few handles (neck, hands, body)
   displace the vertices with a soft falloff; pixels are never regenerated. */
function showScene(page) {
  const note = $("#sceneNote");
  note.textContent = page.scene || `这一页讲的是：${page.text}`;
  note.hidden = !note.hidden;
}
function showSpot(s) {
  const note = $("#sceneNote");
  note.innerHTML = `<b>${esc(s.name)}</b>　${esc(s.desc || "")}`;
  note.hidden = false;
}

/* ------------------------------------------------------------- word card */
/* The unit (word) this character belongs to: page.segments split the text, so
   the story markup can tell us which word was clicked instead of guessing. */
function wordSpan(i, page) {
  const segs = (page.segments && page.segments.length) ? page.segments
    : (page.pinyin || []).map(t => t.ch);
  let at = 0;
  for (const seg of segs) {
    const end = at + seg.length;
    if (i >= at && i < end) {
      const clean = seg.replace(/[，。！？、；：…”’（）《》「」…—]+$/, "");
      return { text: clean, start: at, end };
    }
    at = end;
  }
  return { text: (page.pinyin[i] || {}).ch || "", start: i, end: i + 1 };
}

/* The clause that contains this character, for the "上下文" line. */
function clauseAround(i, page) {
  const text = page.text || "";
  const stops = "，。！？；：…”’\n";
  let s = i, e = i;
  while (s > 0 && !stops.includes(text[s - 1])) s--;
  while (e < text.length - 1 && !stops.includes(text[e])) e++;
  const clause = text.slice(s, Math.min(text.length, e + 1)).trim();
  return clause || text.slice(0, 40);
}

function showWord(i, ruby) {
  const page = state.book.pages[state.index];
  const t = page.pinyin[i];
  if (!t) return;
  const word = state.wordAt[i];
  const span = wordSpan(i, page);
  state.currentWord = word ? word.w : (span.text.length > 1 ? span.text : t.ch);
  $("#wChar").textContent = t.ch;
  $("#wPy").textContent = (word && word.py) || t.py || "";
  $("#wPhrase").textContent = word ? word.w : (span.text.length > 1 ? span.text : "");
  $("#wPhrase").hidden = $("#wPhrase").textContent === "";
  $("#wMean").textContent = word ? word.mean
    : (state.book.glossary && state.book.glossary[t.ch])
      ? state.book.glossary[t.ch]
      : `这个字在这里读「${t.py || "轻声"}」。`;
  const clause = clauseAround(i, page);
  const ctx = $("#wCtx");
  ctx.textContent = clause ? `在这句话里：${clause}` : "";
  ctx.hidden = !clause;
  $$("ruby.r.active").forEach(r => r.classList.remove("active"));
  if (ruby) ruby.classList.add("active");
  const card = $("#wordCard");
  card.hidden = false;
  const r = ruby.getBoundingClientRect();
  const w = 300, h = card.offsetHeight;
  let x = r.left + r.width / 2 - w / 2;
  let y = r.bottom + 12;
  x = Math.max(12, Math.min(x, innerWidth - w - 12));
  if (y + h > innerHeight - 20) y = Math.max(12, r.top - h - 12);
  card.style.left = x + "px"; card.style.top = y + "px";
  $("#wordSay").onclick = () => speak(word ? word.w : t.ch);
}
let currentAudio = null;
async function speak(text) {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  try {
    const a = new Audio(`/api/tts?text=${encodeURIComponent(text)}`);
    currentAudio = a;
    await a.play();
    return;
  } catch (e) { /* fall through to the browser voice */ }
  if (!("speechSynthesis" in window)) return toast("这台设备暂时没有朗读声音");
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "zh-CN"; u.rate = 0.82; u.pitch = 1.02;
  const v = speechSynthesis.getVoices().find(v => /zh|Chinese|Huihui|Xiaoxiao|Yaoyao/i.test(v.lang + v.name));
  if (v) u.voice = v;
  speechSynthesis.speak(u);
}

/* ------------------------------------------------------------- navigation */
function go(i) {
  if (!state.book) return;
  const n = state.book.pages.length;
  state.index = Math.max(0, Math.min(n - 1, i));
  $("#wordCard").hidden = true;
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
$("#btnPrev").onclick = () => go(state.index - 1);
$("#btnNext").onclick = () => go(state.index + 1);
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input,select,textarea")) return;
  if (e.key === "ArrowLeft") go(state.index - 1);
  if (e.key === "ArrowRight") go(state.index + 1);
  if (e.key === "Escape") closeAll();
});
window.addEventListener("resize", () => { if (state.layoutSpots) state.layoutSpots(); });
/* --------------------------------------------------- read-along highlighting
   `reader/narration/<page>.json` carries the per-character timings edge-tts
   reported. The ruby text already has one node per character, so the cues are
   applied by index: a cue that covers a word lights all of its characters. */
const narrationCache = new Map();
async function loadNarration(pageId) {
  if (!pageId) return null;
  if (narrationCache.has(pageId)) return narrationCache.get(pageId);
  const url = `${readerBase()}narration/${encodeURIComponent(pageId)}.json`;
  const data = await fetch(url).then(r => r.ok ? r.json() : null).catch(() => null);
  narrationCache.set(pageId, data);
  return data;
}

let readAlong = { audio: null, cues: [], timer: null, start: 0 };
function stopReadAlong() {
  if (readAlong.audio) { readAlong.audio.pause(); readAlong.audio = null; }
  if (readAlong.timer) { clearTimeout(readAlong.timer); readAlong.timer = null; }
  $$("ruby.r.on").forEach(r => r.classList.remove("on"));
}
function paintCue(tokens, cue) {
  $$("ruby.r.on").forEach(r => r.classList.remove("on"));
  const text = (cue && cue.text) || "";
  if (!text) return;
  // the cue text is one token or a short word: match it in reading order
  let from = tokens.next;
  for (let k = 0; k < text.length; k++) {
    const t = tokens.list[from];
    if (!t) break;
    if (t.ch === text[k]) { t.el.classList.add("on"); from++; }
  }
  if (from === tokens.next) {
    // fall back to "the next N characters"
    for (let k = 0; k < text.length && tokens.list[tokens.next]; k++) {
      tokens.list[tokens.next].el.classList.add("on");
      tokens.next++;
    }
  } else {
    tokens.next = from;
  }
}
async function readAlong_(page) {
  stopReadAlong();
  const data = await loadNarration(page.id);
  const rubies = $$("#story ruby.r");
  const tokens = { list: rubies.map(el => ({ ch: el.querySelector(".ch").textContent, el })), next: 0 };
  if (!data || !data.cues || !data.cues.length || !data.audio) {
    // no timings: keep the old behaviour (whole text read, all tokens dimmed)
    speak(page.text);
    rubies.forEach(r => r.classList.add("speaking"));
    setTimeout(() => $$("ruby.r.speaking").forEach(r => r.classList.remove("speaking")),
               Math.max(1800, page.text.length * 260));
    return;
  }
  const audio = new Audio(`${readerBase()}${data.audio}`);
  readAlong.audio = audio;
  let playing = false;
  try { await audio.play(); playing = true; } catch (e) { /* autoplay blocked */ }
  // The highlight follows the *clock*, not the audio element: if playback is
  // blocked (headless, no device) the words still light up in time, so the page
  // stays usable as a silent read-along.
  let i = 0;
  const start = performance.now();
  const tick = () => {
    const t = (performance.now() - start) / 1000;
    while (i < data.cues.length && data.cues[i].t <= t) {
      paintCue(tokens, data.cues[i]);
      i++;
    }
    if (playing && audio.ended) { stopReadAlong(); return; }
    if (t > (data.duration || 0) + 0.4) { stopReadAlong(); return; }
    readAlong.timer = setTimeout(tick, 40);
  };
  tick();
  if (!playing) speak(page.text);
}

$("#btnRead").onclick = () => {
  const p = state.book.pages[state.index];
  readAlong_(p);
};
$("#btnPinyin").onclick = () => {
  state.pinyin = !state.pinyin;
  document.body.classList.toggle("no-pinyin", !state.pinyin);
  $("#btnPinyin").innerHTML = `拼音 <b>${state.pinyin ? "开" : "关"}</b>`;
};
$("#fontBig").onclick = () => setSize(state.size + 4);
$("#fontSmall").onclick = () => setSize(state.size - 4);
function setSize(v) {
  state.size = Math.max(34, Math.min(68, v));
  document.documentElement.style.setProperty("--story-size", state.size + "px");
}
$("#wordClose").onclick = () => $("#wordCard").hidden = true;
$("#bookSelect").onchange = (e) => openBook(e.target.value);

/* ------------------------------------------------------- character stage */
const readerBase = () => `/books/${encodeURIComponent(state.book.book_id)}/reader/`;

/* curated cards (names, roles) merged with what the cast agents measured */
function castList() {
  const cast = (state.artifacts && state.artifacts.characters) || [];
  const base = state.book.characters || [];
  const out = base.map(c => ({ ...c, ...(cast.find(x => x.id === c.id) || {}) }));
  for (const c of cast) if (!out.some(o => o.id === c.id)) out.push(c);
  return out;
}

/* figures the Rigger cut but nobody could name yet */
function candidateList() {
  return ((state.artifacts && state.artifacts.candidates) || []).map(c => ({
    id: c.id, name: `第 ${String(c.page).replace(/^p0*/, "")} 页的角色`,
    role: "候选切件", desc: "agent 切好了，还没认领。在 curation.json 里写 rig_from 就能用。",
    rig: c.rig, portrait: c.preview || c.rig.art, candidate: true,
  }));
}

$("#btnChars").onclick = () => {
  const chars = castList();
  const cands = candidateList();
  if (!chars.length && !cands.length) return toast("这本书还没有人物卡");
  const base = readerBase();
  $("#charGrid").innerHTML = chars.map(c => {
    const src = c.portrait ? base + c.portrait : (c.image ? base + c.image : "");
    const live = c.rig && c.rig.parts && c.rig.parts.length;
    return `
    <button class="char-card" data-cid="${esc(c.id)}">
      ${src ? `<img src="${esc(src)}" alt="${esc(c.name)}">` : ""}
      <div class="cc-body"><b>${esc(c.name)}</b>${live ? '<em class="cc-live">会动</em>' : ""}<small>${esc(c.role || "")}</small><p>${esc(c.desc || "")}</p></div>
    </button>`;
  }).join("") + (cands.length ? `<div class="cc-group">agent 切好的候选</div>` + cands.map(c => {
    const src = c.portrait ? base + c.portrait : "";
    return `
    <button class="char-card candidate" data-cid="${esc(c.id)}">
      ${src ? `<img src="${esc(src)}" alt="">` : ""}
      <div class="cc-body"><b>${esc(c.name)}</b><em class="cc-live">会动</em><small>${esc(c.role)}</small><p>${esc(c.desc)}</p></div>
    </button>`;
  }).join("") : "");
  $("#charDrawer").hidden = false; $("#modalBackdrop").hidden = false;
  $$(".char-card", $("#charGrid")).forEach(card => {
    card.onclick = () => {
      const c = chars.find(x => x.id === card.dataset.cid)
        || cands.find(x => x.id === card.dataset.cid);
      if (c) openStage(c);
    };
  });
};
$("#charClose").onclick = closeAll;

let beatTimers = [];
let idleAnims = [];

function stopBeats() {
  beatTimers.forEach(clearTimeout);
  beatTimers = [];
  idleAnims.forEach(a => { try { a.cancel(); } catch (_) {} });
  idleAnims = [];
}

/* Rebuild the stage from the rig: body plate + one element per cut-out part. */
function buildStage(c) {
  const stage = $("#puppetStage");
  const base = readerBase();
  const rig = c.rig || {};
  const parts = (rig.parts || []).filter(p => p && p.image);
  const rigged = !!rig.art && parts.length > 0;
  const speech = $("#puppetSpeech");
  stage.className = rigged ? "puppet-stage rigged" : "puppet-stage portrait";
  stage.style.aspectRatio = rigged && rig.art_w && rig.art_h ? `${rig.art_w} / ${rig.art_h}` : "";
  stage.style.background = rigged && rig.key ? `rgb(${rig.key.join(",")})` : "";
  stage.style.transform = "";
  Array.from(stage.querySelectorAll(".puppet-part")).forEach(n => n.remove());

  const body = $("#puppetBody");
  const src = rigged ? base + rig.art : (c.portrait ? base + c.portrait : (c.image ? base + c.image : ""));
  body.hidden = !src;
  if (src) body.src = src;

  parts.forEach(p => {
    const d = document.createElement("div");
    d.className = "puppet-part";
    d.dataset.part = p.name;
    d.style.transformOrigin = `${p.pivot[0] * 100}% ${p.pivot[1] * 100}%`;
    const img = document.createElement("img");
    img.src = base + p.image; img.alt = "";
    d.appendChild(img);
    stage.insertBefore(d, speech);
  });
  return { rig, rigged, parts };
}

function partOf(stage, name) {
  const want = name ? `[data-part="${name}"]` : "";
  return Array.from(stage.querySelectorAll(".puppet-part" + want));
}
function armParts(stage) { return partOf(stage, "").filter(el => el.dataset.part.startsWith("arm_")); }

function swing(el, deg, dur, cycles, hold) {
  const frames = hold
    ? [{ transform: "rotate(0deg)" }, { transform: `rotate(${deg}deg)` }]
    : [{ transform: "rotate(0deg)" }, { transform: `rotate(${deg}deg)`, offset: .35 },
       { transform: `rotate(${deg * .45}deg)`, offset: .65 }, { transform: "rotate(0deg)" }];
  try {
    return el.animate(frames, {
      duration: Math.max(120, dur * 1000), iterations: hold ? 1 : (cycles || 1),
      easing: "ease-in-out", fill: hold ? "forwards" : "none"
    });
  } catch (_) { el.style.transform = `rotate(${deg}deg)`; return null; }
}

function runBeat(stage, beat) {
  const bubble = $("#puppetSpeech");
  const act = beat.action;
  if (act === "wave") {
    armParts(stage).forEach((el, i) => {
      const sign = el.dataset.part.endsWith("l") ? 1 : -1;
      swing(el, sign * (beat.amp || 18), beat.dur || 1.1, beat.cycles || 2);
    });
  } else if (act === "point" || act === "shrug") {
    const arms = armParts(stage);
    const el = arms.find(a => a.dataset.part.endsWith(beat.side === "left" ? "l" : "r")) || arms[0];
    if (el) swing(el, (el.dataset.part.endsWith("l") ? 1 : -1) * (beat.amp || 12), 0.5, 1, true);
  } else if (act === "nod") {
    const head = partOf(stage, "head")[0];
    if (head) { stage.style.setProperty("--nod", `${beat.amp || 4}deg`); swing(head, beat.amp || 4, 0.5, 1.6); }
  } else if (act === "bounce") {
    const body = $("#puppetBody");
    try { body.animate([{ transform: "translateY(0)" }, { transform: `translateY(-${(beat.amp || 3) * 2}px)` }, { transform: "translateY(0)" }], { duration: 520, iterations: 2, easing: "ease-in-out" }); } catch (_) {}
  } else if (act === "speak") {
    bubble.textContent = beat.text || "……";
    bubble.classList.add("on");
    clearTimeout(runBeat._bubble);
    runBeat._bubble = setTimeout(() => bubble.classList.remove("on"), Math.max(1400, (beat.dur || 1.6) * 1000));
  }
}

const motionCache = new Map();
async function loadMotion(pageId) {
  if (!pageId) return null;
  if (motionCache.has(pageId)) return motionCache.get(pageId);
  const url = `${readerBase()}motion/${encodeURIComponent(pageId)}.json`;
  const plan = await fetch(url).then(r => r.ok ? r.json() : null).catch(() => null);
  motionCache.set(pageId, plan);
  return plan;
}

async function playPageBeats(stage, page) {
  stopBeats();
  if (!stage.classList.contains("rigged") || !page) return;
  const plan = await loadMotion(page.id);
  if (!plan || !plan.beats) return;
  for (const b of plan.beats) {
    if (b.action === "idle") continue;
    beatTimers.push(setTimeout(() => runBeat(stage, b), Math.max(0, b.t * 1000)));
  }
}

function idleSway(stage) {
  armParts(stage).forEach(el => {
    const sign = el.dataset.part.endsWith("l") ? 1 : -1;
    try {
      idleAnims.push(el.animate(
        [{ transform: `rotate(${-sign * 1.2}deg)` }, { transform: `rotate(${sign * 2}deg)` }],
        { duration: 3800 + Math.random() * 900, iterations: Infinity, direction: "alternate", easing: "ease-in-out" }));
    } catch (_) {}
  });
}

function openStage(c) {
  const stage = $("#puppetStage");
  const { rig, rigged } = buildStage(c);
  $("#puppetName").textContent = c.name;
  $("#puppetRole").textContent = c.role || "角色";
  $("#puppetWave").hidden = !rigged || !(rig.parts || []).some(p => p.name.startsWith("arm_"));
  $("#charDrawer").hidden = true;
  $("#puppetModal").hidden = false;
  $("#modalBackdrop").hidden = false;

  const bubble = $("#puppetSpeech");
  bubble.classList.remove("on");
  bubble.textContent = rigged
    ? `我是${c.name}。${c.desc || ""}`
    : `你好，我是${c.name}。${c.desc || ""}`;
  setTimeout(() => bubble.classList.add("on"), 240);
  stage.onclick = () => bubble.classList.toggle("on");

  if (rigged) {
    idleSway(stage);
    playPageBeats(stage, state.book.pages[state.index]);
    if (!stage._gaze) {
      stage.addEventListener("mousemove", (e) => {
        const r = stage.getBoundingClientRect();
        const dx = (e.clientX - r.left) / r.width - .5;
        const dy = (e.clientY - r.top) / r.height - .5;
        stage.style.transform = `perspective(900px) rotateY(${dx * 5}deg) rotateX(${-dy * 3}deg)`;
      });
      stage.addEventListener("mouseleave", () => { stage.style.transform = ""; });
      stage._gaze = true;
    }
  }

  $("#puppetWave").onclick = () => {
    const amp = Math.max(...(rig.parts || []).filter(p => p.name.startsWith("arm_")).map(p => p.amp || 18), 18);
    armParts(stage).forEach(el => {
      const sign = el.dataset.part.endsWith("l") ? 1 : -1;
      swing(el, sign * amp, 1.15, 2);
    });
    bubble.textContent = `嗨，我是${c.name}！`;
    bubble.classList.add("on");
  };
}
$("#puppetClose").onclick = closeAll;
$("#puppetBack").onclick = closeAll;

/* ------------------------------------------------------------- modals */
function closeAll() {
  stopBeats();
  $("#charDrawer").hidden = true;
  $("#puppetModal").hidden = true;
  $("#videoModal").hidden = true;
  $("#importModal").hidden = true;
  $("#modalBackdrop").hidden = true;
  speechSynthesis.cancel();
}
$("#videoClose").onclick = closeAll;
$("#importClose").onclick = closeAll;
$("#modalBackdrop").onclick = closeAll;

$("#btnVideo").onclick = () => {
  $("#videoModal").hidden = false; $("#modalBackdrop").hidden = false;
  $("#videoPlan").innerHTML = `<span>${esc(state.book.title)}</span><span>${state.book.pages.length} 页</span><span>16:9</span><span>大字 + 拼音</span><span>2.5D 运镜</span>`;
};
let videoJob = null;
$("#btnMakeFilm").onclick = async () => {
  $("#btnMakeFilm").disabled = true;
  $("#videoResult").hidden = true;
  try {
    const { job } = await api("/api/video", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book: state.book.book_id }) });
    videoJob = job; pollVideo();
  } catch (e) { toast(e.message); $("#btnMakeFilm").disabled = false; }
};
async function pollVideo() {
  const j = await api(`/api/job?id=${videoJob}`);
  $("#videoBar").style.width = Math.round((j.progress || 0) * 100) + "%";
  $("#videoStageLine").textContent = j.stage || "生成中";
  if (j.status === "done") {
    const path = j.result.path;
    $("#filmPlayer").src = `/media/${path}`;
    $("#filmDownload").href = `/media/${path}`;
    $("#videoResult").hidden = false;
    $("#btnMakeFilm").disabled = false;
    $("#btnMakeFilm").textContent = "再生成一次";
    toast("影片生成好了");
    return;
  }
  if (j.status === "error") {
    $("#videoStageLine").textContent = "生成失败：" + (j.error || "未知错误");
    $("#btnMakeFilm").disabled = false;
    return;
  }
  setTimeout(pollVideo, 900);
}

/* ------------------------------------------------------------- import */
let importFiles = [];
$("#btnImport").onclick = () => {
  $("#importModal").hidden = false; $("#modalBackdrop").hidden = false;
};
$("#dropzone").onclick = () => $("#fileInput").click();
$("#fileInput").onchange = (e) => setImportFiles(Array.from(e.target.files));
$("#dropzone").ondragover = (e) => { e.preventDefault(); $("#dropzone").classList.add("over"); };
$("#dropzone").ondragleave = () => $("#dropzone").classList.remove("over");
$("#dropzone").ondrop = (e) => {
  e.preventDefault(); $("#dropzone").classList.remove("over");
  setImportFiles(Array.from(e.dataTransfer.files).filter(f => f.type.startsWith("image/")));
};
function setImportFiles(files) {
  importFiles = files.sort((a, b) => a.lastModified - b.lastModified);
  $("#fileHint").textContent = importFiles.length ? `已选择 ${importFiles.length} 页，按文件时间排序` : "支持 jpg / png，按文件时间排序";
  $("#fileStrip").innerHTML = importFiles.map(f => `<img src="${URL.createObjectURL(f)}" alt="">`).join("");
  $("#btnDoImport").disabled = !importFiles.length;
  if (!$("#importTitle").value) $("#importTitle").value = (importFiles[0]?.name || "").replace(/\.(jpe?g|png)$/i, "");
}
$("#btnDoImport").onclick = async () => {
  const title = $("#importTitle").value.trim() || "未命名绘本";
  $("#btnDoImport").disabled = true;
  $("#importStage").hidden = false;
  $("#importStageLine").textContent = "正在上传页面…";
  try {
    const files = await Promise.all(importFiles.map(f => new Promise((res) => {
      const r = new FileReader(); r.onload = () => res({ name: f.name, lastModified: f.lastModified, data: r.result }); r.readAsDataURL(f);
    })));
    const { job } = await api("/api/import", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, files }) });
    const tick = async () => {
      const j = await api(`/api/job?id=${job}`);
      $("#importBar").style.width = Math.round((j.progress || 0) * 100) + "%";
      $("#importStageLine").textContent = j.stage || "分析中";
      if (j.status === "done") {
        toast("互动绘本做好啦");
        await loadBooks(j.result.book);
        closeAll();
        return;
      }
      if (j.status === "error") { $("#importStageLine").textContent = "失败：" + j.error; $("#btnDoImport").disabled = false; return; }
      setTimeout(tick, 800);
    };
    tick();
  } catch (e) { $("#importStageLine").textContent = "失败：" + e.message; $("#btnDoImport").disabled = false; }
};

/* ------------------------------------------------------------- boot */
speechSynthesis.getVoices();
if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = () => {};
loadBooks().catch(e => toast("加载失败：" + e.message));
