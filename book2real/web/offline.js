/* Offline pack for the picture-book reader.
 *
 * Zero build, zero dependencies, browser-native ESM.
 *
 * Honesty rule (docs/INTEGRATION_CONTRACT.md §3): Service Workers only exist in
 * a secure context. Served over plain http:// on a LAN address the browser will
 * simply refuse to register one, so `offlineStatus().available` is false and the
 * UI says "离线缓存不可用（需要 https 或 localhost）" instead of pretending.
 *
 * Cache layout: every book asset goes into the SAME cache the service worker
 * uses (`b2r-cache-<CACHE_V>` in sw.js), keyed by its real URL, so a cached
 * page is a cache hit with or without the SW alive.
 */

const CACHE_V = "b2r-v1";                     // keep in sync with sw.js
const CACHE_NAME = `b2r-cache-${CACHE_V}`;
const STORE_KEY = "b2r.offline.books";
const INSECURE_REASON = "离线缓存不可用（需要 https 或 localhost）";

/* --------------------------------------------------------------- storage */
function readStore() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter(r => r && r.id) : [];
  } catch (_) { return []; }
}
function writeStore(list) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(list.slice(0, 40))); return true; }
  catch (_) { return false; }
}
function recordFor(id) { return readStore().find(r => r.id === id) || null; }

/* --------------------------------------------------------------- status */
export function offlineStatus() {
  const secure = typeof isSecureContext !== "undefined"
    ? !!isSecureContext
    : (typeof location !== "undefined" &&
       (location.protocol === "https:" || /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname || "")));
  const hasSW = typeof navigator !== "undefined" && !!navigator.serviceWorker;
  const hasCache = typeof caches !== "undefined";
  const controlled = !!(hasSW && navigator.serviceWorker.controller);
  const books = readStore();
  let reason = "";
  if (!secure) reason = INSECURE_REASON;
  else if (!hasSW) reason = "这台浏览器不支持 Service Worker，离线缓存不可用";
  else if (!hasCache) reason = "这台浏览器不支持 Cache Storage，离线缓存不可用";
  else if (initOffline._regError) reason = "Service Worker 注册失败：" + initOffline._regError;
  return {
    available: secure && hasSW && hasCache,
    secureContext: secure,
    serviceWorker: hasSW,
    registered: !!initOffline._reg,
    controlled,
    cacheName: CACHE_NAME,
    cacheVersion: CACHE_V,
    books: books.map(b => b.id),
    reason,
  };
}

/* Map of /books/<id>/reader/... -> absolute URL, so cache keys are stable. */
function bookBase(id) {
  const origin = typeof location !== "undefined" ? location.origin : "http://localhost";
  return `${origin}/books/${encodeURIComponent(id)}/`;
}

/* --------------------------------------------------------------- download */
export async function downloadBook(id, onProgress) {
  const st = offlineStatus();
  if (!st.available) { const e = new Error(st.reason || INSECURE_REASON); e.code = "unavailable"; throw e; }
  if (!id) throw new Error("还没有选书");

  let manifest;
  try {
    const res = await fetch(`/api/books/${encodeURIComponent(id)}/files`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const ctype = (res.headers.get("content-type") || "").toLowerCase();
    if (!ctype.includes("json")) throw new Error("返回的不是 JSON");
    manifest = await res.json();
  } catch (e) {
    const err = new Error(`服务端没有 /api/books/<id>/files（${(e && e.message) || "请求失败"}）`);
    err.code = "no_manifest";
    throw err;
  }

  const files = Array.isArray(manifest && manifest.files)
    ? manifest.files.filter(f => f && f.path) : [];
  if (!files.length) throw new Error("这本书的 reader 目录是空的，没有可下载的内容");

  const cache = await caches.open(CACHE_NAME);
  const base = bookBase(id);
  const failed = [];
  let done = 0, bytes = 0;
  const report = (path) => {
    if (typeof onProgress === "function") {
      try { onProgress({ done, total: files.length, path, bytes, failed: failed.length }); } catch (_) {}
    }
  };
  report("");
  for (const f of files) {
    const url = base + String(f.path).replace(/^\/+/, "");
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await cache.put(new Request(url), r.clone());
      bytes += Number(f.bytes) || 0;
    } catch (e) {
      failed.push({ path: f.path, error: (e && e.message) || "下载失败" });
    }
    done++;
    report(f.path);
  }

  const store = readStore().filter(r => r.id !== id);
  store.push({ id, at: Date.now(), files: files.map(f => f.path), bytes, failed: failed.length });
  writeStore(store);
  return { book: id, files: files.length, bytes, failed };
}

/* --------------------------------------------------------------- verify */
export async function verifyBook(id) {
  const st = offlineStatus();
  if (!st.available) return { ok: false, reason: st.reason, files: 0, missing: [] };
  const rec = recordFor(id);
  if (!rec) return { ok: false, reason: "这本书没有下载记录", files: 0, missing: [] };
  if (typeof caches === "undefined") return { ok: false, reason: st.reason, files: 0, missing: [] };
  const cache = await caches.open(CACHE_NAME);
  const base = bookBase(id);
  const missing = [];
  for (const p of rec.files || []) {
    const url = base + String(p).replace(/^\/+/, "");
    const hit = await cache.match(new Request(url));
    if (!hit) missing.push(p);
  }
  return { ok: missing.length === 0, files: (rec.files || []).length, missing, bytes: rec.bytes || 0 };
}

export async function removeBook(id) {
  if (typeof caches === "undefined") return false;
  const cache = await caches.open(CACHE_NAME);
  const prefix = `/books/${encodeURIComponent(id)}/`;
  const keys = await cache.keys();
  let removed = 0;
  for (const req of keys) {
    let path = "";
    try { path = new URL(req.url).pathname; } catch (_) { continue; }
    if (path.startsWith(prefix)) { await cache.delete(req); removed++; }
  }
  writeStore(readStore().filter(r => r.id !== id));
  return removed > 0;
}

/* --------------------------------------------------------------- UI glue */
function el(id) { return typeof document === "undefined" ? null : document.getElementById(id); }

function currentBookId() {
  try {
    const saved = localStorage.getItem("b2r.book");
    if (saved) return saved;
    if (typeof location !== "undefined") {
      const q = new URLSearchParams(location.search).get("book");
      if (q) return q;
    }
  } catch (_) {}
  return "";
}

function paintStatus() {
  const st = offlineStatus();
  const note = el("offlineNote"), stat = el("offlineStat");
  const dl = el("offlineDownload"), list = el("offlineList"), btn = el("btnOffline");
  if (btn) {
    btn.title = st.available
      ? (st.registered ? "离线缓存已就绪" : "准备离线缓存")
      : (st.reason || INSECURE_REASON);
  }
  if (note) {
    note.textContent = st.available
      ? "把这一页的图和这本书的阅读包存进本机缓存，断网后刷新仍能翻页、看图、听已下载的句子。"
      : (st.reason || INSECURE_REASON);
    note.classList.toggle("offline-warn", !st.available);
  }
  if (stat) {
    stat.textContent = st.available
      ? `缓存 ${st.books.length ? `已存 ${st.books.length} 本` : "还没存书"}${st.controlled ? " · SW 已接管" : st.registered ? " · SW 已注册" : " · SW 未接管"}`
      : "离线缓存不可用";
  }
  if (dl) {
    dl.disabled = !st.available;
    dl.title = st.available ? "下载当前这一本" : (st.reason || INSECURE_REASON);
  }
  if (list) {
    list.innerHTML = st.books.length
      ? st.books.map(b => `<div class="offline-book"><b>${b}</b>
          <button class="tool tiny offline-verify" data-id="${b}">检查</button>
          <button class="tool tiny offline-remove" data-id="${b}">删除</button></div>`).join("")
      : `<div class="stage-line">还没有下载过整本。</div>`;
    list.querySelectorAll(".offline-verify").forEach(b => b.onclick = async () => {
      const r = await verifyBook(b.dataset.id);
      const line = el("offlineStage");
      if (line) line.textContent = r.ok ? `《${b.dataset.id}》完整：${r.files} 个文件` : `缺 ${r.missing.length} 个文件`;
    });
    list.querySelectorAll(".offline-remove").forEach(b => b.onclick = async () => {
      await removeBook(b.dataset.id);
      paintStatus();
    });
  }
  return st;
}

function openModal() {
  const m = el("offlineModal"), backdrop = el("modalBackdrop");
  if (!m) return;
  m.hidden = false;
  if (backdrop) backdrop.hidden = false;
  const st = paintStatus();
  const line = el("offlineStage");
  if (line) {
    line.textContent = st.available
      ? (st.registered ? "服务线程已注册。" : "还没有注册服务线程（刷新一次即可）。")
      : (st.reason || INSECURE_REASON);
  }
}

function otherModalOpen() {
  return ["puppetModal", "videoModal", "importModal"].some(id => {
    const n = el(id);
    return n && !n.hidden;
  }) || (el("qaPanel") && !el("qaPanel").hidden);
}

function closeModal() {
  const m = el("offlineModal");
  if (m) m.hidden = true;
  const backdrop = el("modalBackdrop");
  if (backdrop && !otherModalOpen()) backdrop.hidden = true;
}

async function doDownload() {
  const bar = el("offlineBar"), line = el("offlineStage"), dl = el("offlineDownload");
  const id = currentBookId();
  if (!id) { if (line) line.textContent = "先打开一本书，再回来下载。"; return; }
  if (dl) dl.disabled = true;
  try {
    const r = await downloadBook(id, p => {
      if (bar) bar.style.width = p.total ? Math.round((p.done / p.total) * 100) + "%" : "0%";
      if (line) line.textContent = p.path ? `已存 ${p.done}/${p.total}：${p.path}` : "准备下载…";
    });
    if (line) {
      line.textContent = r.failed.length
        ? `下载完成，但 ${r.failed.length} 个文件没存上（刷新后会自动重试或点"检查"）`
        : `下载完成：${r.files} 个文件，约 ${(r.bytes / 1024 / 1024).toFixed(1)} MB`;
    }
  } catch (e) {
    if (line) line.textContent = (e && e.message) || "下载失败";
  } finally {
    if (dl) dl.disabled = !offlineStatus().available;
    paintStatus();
  }
}

/* Register the service worker (only in a secure context) and wire the UI.
 * Safe to call twice; the second call only repaints. */
export async function initOffline() {
  const st = offlineStatus();
  paintStatus();

  const btn = el("btnOffline");
  if (btn) btn.onclick = openModal;
  const close = el("offlineClose");
  if (close) close.onclick = closeModal;
  const dl = el("offlineDownload");
  if (dl) dl.onclick = doDownload;

  if (!st.available) {
    // honest: say why, do not register, do not pretend
    return offlineStatus();
  }

  try {
    initOffline._reg = await navigator.serviceWorker.register("/static/sw.js", { scope: "/" });
    initOffline._regError = "";
    try {
      await navigator.serviceWorker.ready;
      initOffline._reg.update().catch(() => {});
    } catch (_) {}
  } catch (e) {
    initOffline._reg = null;
    initOffline._regError = (e && e.message) || "注册被拒绝";
  }
  paintStatus();
  return offlineStatus();
}

initOffline._reg = null;
initOffline._regError = "";

export default { initOffline, downloadBook, verifyBook, removeBook, offlineStatus };
