/* Sentence-level TTS for the picture-book reader.
 *
 * Zero build, zero dependencies, browser-native ESM.
 *
 * L2 (default): split the page text into sentences, ask the server for each
 * sentence's audio (`/api/tts?text=`), play them one after another. The server
 * has a disk cache, so a second read of the same sentence synthesises nothing.
 * L1 (fallback): if the server is missing / 503 / not audio, the browser's own
 * `speechSynthesis` reads that sentence instead. `reader.js` keeps the old
 * one-shot `speechSynthesis` path as its own last resort.
 *
 * The two pure functions (`splitSentences`, `interpChars`) are exported so they
 * can be unit-tested with plain node, no DOM needed.
 */

const HARD_STOP = /[。！？!?；;…\n]/;
const SOFT_KEEP = /[，,、：:—]/;
const MAX_SENTENCE = 60;

/* "小劳拉说，我们去找豚鼠吧！波特不见了。" -> ["...吧！", "波特不见了。"] */
export function splitSentences(text) {
  const s = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
  if (!s) return [];
  const raw = [];
  let buf = "";
  for (const ch of s) {
    buf += ch;
    if (HARD_STOP.test(ch)) { raw.push(buf.trim()); buf = ""; }
  }
  if (buf.trim()) raw.push(buf.trim());

  // a 1-3 char fragment ("好！") is glued to its neighbour: fewer requests, and
  // the reader does not stop-and-go on every shout in the story.
  const merged = [];
  for (const part of raw) {
    const solid = part.replace(/[^\u4e00-\u9fa5a-zA-Z0-9]/g, "");
    if (merged.length && solid.length < 4) merged[merged.length - 1] += part;
    else merged.push(part);
  }

  // long unpunctuated stretches still get cut, so one sentence never becomes a
  // 200-character request (the server truncates at 400 anyway).
  const out = [];
  for (const part of merged) {
    if (part.length <= MAX_SENTENCE) { out.push(part); continue; }
    for (let i = 0; i < part.length; i += MAX_SENTENCE) out.push(part.slice(i, i + MAX_SENTENCE));
  }
  return out;
}

/* Character index for time t inside a sentence of `chars` characters that takes
 * `dur` seconds. Chinese `onboundary` is unreliable (crbug 40715888) and absent
 * on Android WebView, so the read-along highlight is interpolated by character
 * count, not by real timings. */
export function interpChars(chars, t, dur) {
  const n = Math.floor(Number(chars) || 0);
  if (n <= 0) return 0;
  const d = Number(dur);
  if (!Number.isFinite(d) || d <= 0) return 0;
  const ratio = Math.max(0, Math.min(1, (Number(t) || 0) / d));
  return Math.max(0, Math.min(n - 1, Math.floor(ratio * n)));
}

/* ------------------------------------------------------------------ state */
let audioEl = null;
let generation = 0;          // bumping it cancels whatever is playing
let active = false;          // is speakText() currently reading?
let startedAt = 0;           // ms timestamp of the last playback start

export function speaking() { return active; }

/* ms since playback started (Infinity when nothing is playing) — the voice
 * layer uses it for the 600 ms echo guard, so our own loudspeaker tail is not
 * mistaken for the user talking. */
export function sinceStart() { return active && startedAt ? Date.now() - startedAt : Infinity; }

/* Silence everything this module started, and the browser voice too. */
export function stop() {
  generation++;
  active = false;
  if (audioEl) { try { audioEl.pause(); } catch (_) {} audioEl = null; }
  if (typeof window !== "undefined" && window.speechSynthesis) {
    try { window.speechSynthesis.cancel(); } catch (_) {}
  }
}

async function fetchAudio(text) {
  const r = await fetch(`/api/tts?text=${encodeURIComponent(text)}`, {
    headers: { Accept: "audio/*,application/json" },
  });
  if (!r.ok) {
    // the server contract says 503 + {"error","fallback":"l1"} — never an HTML 500
    const j = await r.json().catch(() => ({}));
    const err = new Error(j.error || `tts ${r.status}`);
    err.fallback = j.fallback || (r.status === 503 ? "l1" : "");
    throw err;
  }
  const ctype = (r.headers.get("content-type") || "").toLowerCase();
  if (ctype.includes("json")) {           // "ok" JSON instead of audio
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error || "tts returned json");
  }
  return r.blob();
}

function playBlob(blob) {
  return new Promise((resolve, reject) => {
    let url = "";
    try { url = URL.createObjectURL(blob); } catch (e) { return reject(e); }
    const a = new Audio(url);
    audioEl = a;
    let done = false;
    const finish = (err) => {
      if (done) return;
      done = true;
      audioEl = null;
      try { URL.revokeObjectURL(url); } catch (_) {}
      err ? reject(err) : resolve();
    };
    a.onended = () => finish(null);
    a.onerror = () => finish(new Error("audio error"));
    a.play().catch((e) => finish(e));
  });
}

/* Browser voice, used both as L1 fallback and when the server has no engine. */
function speakBrowser(text) {
  return new Promise((resolve) => {
    if (typeof window === "undefined" || !window.speechSynthesis ||
        typeof SpeechSynthesisUtterance === "undefined") return resolve(false);
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "zh-CN"; u.rate = 0.82; u.pitch = 1.02;
    const voices = window.speechSynthesis.getVoices() || [];
    const v = voices.find(x => /zh|Chinese|Huihui|Xiaoxiao|Yaoyao/i.test(x.lang + x.name));
    if (v) u.voice = v;
    let settled = false;
    const finish = () => { if (!settled) { settled = true; resolve(true); } };
    u.onend = finish;
    u.onerror = finish;
    try { window.speechSynthesis.speak(u); } catch (_) { finish(); }
    // an engine that never fires onend must not hang the reader forever
    setTimeout(finish, Math.max(5000, text.length * 400));
  });
}

/* Read `text` sentence by sentence. Resolves when done or cancelled; never
 * rejects — the reader's UI should not need a try/catch around it. */
export async function speakText(text, opts = {}) {
  stop();
  const mine = generation;
  active = true;
  startedAt = Date.now();
  const parts = splitSentences(text);
  try {
    for (let i = 0; i < parts.length; i++) {
      if (generation !== mine) return;
      const part = parts[i];
      let engine = "l2";
      try {
        await playBlob(await fetchAudio(part));
      } catch (e) {
        if (generation !== mine) return;
        engine = e && e.fallback === "l1" ? "l1" : "l1";
        await speakBrowser(part);
      }
      if (typeof opts.onSentence === "function") {
        try { opts.onSentence(part, i, parts.length, engine); } catch (_) {}
      }
      if (generation !== mine) return;
    }
  } finally {
    if (generation === mine) active = false;
  }
}

export default { speakText, stop, splitSentences, interpChars, speaking };
