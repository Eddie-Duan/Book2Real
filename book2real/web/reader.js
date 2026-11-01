/* Interactive picture-book reader. No framework, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const state = { books: [], book: null, index: 0, pinyin: true, size: 46, wordAt: [], currentWord: null };

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
  if (probe === "video") setTimeout(() => $("#btnVideo").click(), 250);
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

function renderArt(page) {
  const layers = $("#artLayers");
  layers.innerHTML = "";
  const base = `/books/${encodeURIComponent(state.book.book_id)}/reader/`;
  if (page.art_fg && page.art_bg) {
    layers.innerHTML = `<div class="art-img art-bg" style="background-image:url('${base}${esc(page.art_bg)}')"></div>
      <div class="art-img art-fg" style="background-image:url('${base}${esc(page.art_fg)}')"></div>`;
  } else {
    layers.innerHTML = `<div class="art-img art-one" style="background-image:url('${base}${esc(page.art)}')"></div>`;
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
  spots.innerHTML = "";
  (page.spots || []).forEach((s, i) => {
    const btn = document.createElement("button");
    btn.className = "spot"; btn.dataset.i = i;
    btn.innerHTML = `<i></i><span>${esc(s.name)}</span>`;
    btn.onclick = (e) => { e.stopPropagation(); showSpot(s); };
    spots.appendChild(btn);
  });
  state.layoutSpots = () => {
    const stage = $("#artStage"); const r = stage.getBoundingClientRect();
    const ar = (page.art_w || 4) / (page.art_h || 3);
    let w = r.width, h = w / ar;
    if (h > r.height) { h = r.height; w = h * ar; }
    const ox = (r.width - w) / 2, oy = (r.height - h) / 2;
    $$(".spot", spots).forEach((btn, i) => {
      const b = (page.spots[i] && page.spots[i].box) || [0, 0, .1, .1];
      btn.style.left = (ox + (b[0] + b[2] / 2) * w) + "px";
      btn.style.top = (oy + (b[1] + b[3] / 2) * h) + "px";
    });
  };
  state.layoutSpots();
  $("#sceneChip").style.display = (page.spots || []).length ? "none" : "flex";

  if (!stage._parallax) {
    stage.addEventListener("mousemove", (e) => {
      const r = stage.getBoundingClientRect();
      const dx = (e.clientX - r.left) / r.width - 0.5;
      const dy = (e.clientY - r.top) / r.height - 0.5;
      const bg = $(".art-bg"), fg = $(".art-fg");
      if (bg) bg.style.transform = `scale(1.07) translate(${dx * -10}px,${dy * -8}px)`;
      if (fg) fg.style.transform = `scale(1.02) translate(${dx * -22}px,${dy * -16}px)`;
    });
    stage.addEventListener("mouseleave", () => {
      const bg = $(".art-bg"), fg = $(".art-fg");
      if (bg) bg.style.transform = "";
      if (fg) fg.style.transform = "";
    });
    stage._parallax = true;
  }
}

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
function showWord(i, ruby) {
  const page = state.book.pages[state.index];
  const t = page.pinyin[i];
  if (!t) return;
  const word = state.wordAt[i];
  state.currentWord = word ? word.w : t.ch;
  $("#wChar").textContent = t.ch;
  $("#wPy").textContent = (word && word.py) || t.py || "";
  $("#wPhrase").textContent = word ? word.w : "本页生字";
  $("#wMean").textContent = word ? word.mean
    : (state.book.glossary && state.book.glossary[t.ch])
      ? state.book.glossary[t.ch]
      : `这个字在本页读作「${t.py || "轻声"}」。点一点旁边的词语，还能看到整句的意思。`;
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
$("#btnRead").onclick = () => {
  const p = state.book.pages[state.index];
  speak(p.text);
  $$("ruby.r").forEach(r => r.classList.add("speaking"));
  setTimeout(() => $$("ruby.r.speaking").forEach(r => r.classList.remove("speaking")), Math.max(1800, p.text.length * 260));
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

/* ------------------------------------------------------------- character drawer */
$("#btnChars").onclick = () => {
  const chars = state.book.characters || [];
  if (!chars.length) return toast("这本书还没有人物卡");
  const base = `/books/${encodeURIComponent(state.book.book_id)}/reader/`;
  $("#charGrid").innerHTML = chars.map(c => `
    <button class="char-card" data-cid="${esc(c.id)}">
      ${c.image ? `<img src="${base}${esc(c.image)}" alt="${esc(c.name)}">` : ""}
      <div class="cc-body"><b>${esc(c.name)}</b><small>${esc(c.role || "")}</small><p>${esc(c.desc || "")}</p></div>
    </button>`).join("");
  $("#charDrawer").hidden = false; $("#modalBackdrop").hidden = false;
  $$(".char-card", $("#charGrid")).forEach(card => {
    card.onclick = () => {
      const c = chars.find(x => x.id === card.dataset.cid);
      if (c) toast(`${c.name} · ${c.role || ""}：${c.desc || ""}`, 4200);
    };
  });
};
$("#charClose").onclick = closeAll;

/* ------------------------------------------------------------- modals */
function closeAll() {
  $("#charDrawer").hidden = true;
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
