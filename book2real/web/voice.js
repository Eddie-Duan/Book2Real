/* Voice input for the picture-book reader: Web Speech ASR + barge-in.
 *
 * Zero build, zero dependencies, browser-native ESM.
 *
 * Capability is different from "it is in the browser API": Chrome/Edge desktop
 * give partial results, **Android WebView does not implement SpeechRecognition
 * at all** (docs/NN_ACADEMY_REUSE.md §2), Firefox ships it off by default. So
 * `supported()` is checked before the button is enabled, and the button gets a
 * `title` that says why it is greyed out — a dead button is not acceptable.
 *
 * Barge-in follows docs/INTEGRATION_CONTRACT.md §1.6. `decide()` below is a
 * line-for-line mirror of `book2real/voice_state.py:decide` (the tested truth
 * source), including the non-mandated branches, so the two sides can be diffed
 * event by event instead of drifting apart:
 *   speaking 且 partial/final 去空白后 >=2 字 且 since_cancel_ms > 600 → cancel
 *   thinking 时收到 partial → cancel
 *   idle/listening 收到 final → listen（提交）
 * `since_cancel_ms` is read as "ms since the last speaking-start / cancel
 * moment": right after speech starts, and right after a cancel, the 600 ms
 * window swallows the loudspeaker echo instead of cancelling itself.
 */
import { speaking as ttsSpeaking, sinceStart as ttsSinceStart, stop as stopTts } from "./tts.js";

export const ECHO_GUARD_MS = 600;
export const MIN_BARGE_CHARS = 2;
export const PHASES = ["idle", "listening", "thinking", "speaking"];
export const EVENT_TYPES = ["partial", "final", "speech_start", "speech_end", "answer_ready"];
export const DECISIONS = ["speak", "cancel", "wait", "listen"];

function Rec() {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export function supported() { return !!Rec(); }

/* Human-readable reason for the greyed-out microphone button. */
export function unsupportedReason() {
  if (typeof window === "undefined") return "这里没有浏览器语音识别环境";
  const ua = (typeof navigator !== "undefined" && navigator.userAgent) || "";
  if (/Android/i.test(ua) && /\bwv\b/i.test(ua)) {
    return "Android WebView 不支持语音识别（APK 里请用打字提问或预录音频）";
  }
  if (/Firefox/i.test(ua)) return "Firefox 默认没有开启语音识别，可以用打字提问";
  return "这台设备的浏览器不提供语音识别（Chrome / Edge 桌面版可用），可以打字提问";
}

const cleanText = (text) => String(text == null ? "" : text).replace(/\s+/g, "");

/* §1.6 状态机，纯函数，1:1 镜像 book2real/voice_state.py:decide
 * （Python 侧是这个仓库被测试的真相源）。返回 {decision,phase,text,chars,reason}。
 * 多余的分支都是保守的 wait，宁可不动也不能抢话。 */
export function decide(state, event) {
  const st = state || {};
  const ev = event || {};
  let phase = String(st.phase || "idle");
  if (!PHASES.includes(phase)) phase = "idle";
  let since = parseInt(st.since_cancel_ms, 10);
  if (!Number.isFinite(since) || since < 0) since = 0;

  const etype = String(ev.type || "");
  const text = String(ev.text == null ? "" : ev.text).trim();
  const clean = cleanText(text);
  const longEnough = clean.length >= MIN_BARGE_CHARS;
  const echoGuarded = since <= ECHO_GUARD_MS;
  const out = (decision, nextPhase, why) => ({
    decision, phase: nextPhase, text, chars: clean.length, reason: why,
  });

  if (etype === "partial") {
    if (phase === "speaking") {
      if (!longEnough) return out("wait", "speaking", "too_short");
      if (echoGuarded) return out("wait", "speaking", "echo_guard");
      return out("cancel", "listening", "barge_in");
    }
    if (phase === "thinking") return out("cancel", "listening", "barge_in_thinking");
    return out("wait", phase, "partial");
  }

  if (etype === "final") {
    if (phase === "speaking") {
      if (!longEnough) return out("wait", "speaking", "too_short");
      if (echoGuarded) return out("wait", "speaking", "echo_guard");
      return out("cancel", "listening", "barge_in_final");
    }
    if (phase === "thinking") return out("wait", "thinking", "already_thinking");
    return out("listen", "listening", "commit");
  }

  if (etype === "answer_ready") {
    if (phase === "idle" || phase === "thinking") return out("speak", "speaking", "answer");
    if (phase === "listening") return out("wait", "listening", "user_speaking");
    return out("wait", "speaking", "already_speaking");
  }

  if (etype === "speech_start") {
    if (phase === "speaking") return out("wait", "speaking", "already_speaking");
    if (phase === "listening") return out("wait", "listening", "user_speaking");
    return out("speak", "speaking", "speech_start");
  }

  if (etype === "speech_end") {
    if (phase === "speaking") return out("listen", "listening", "speech_done");
    return out("wait", phase, "speech_end");
  }

  return out("wait", phase, "unknown_event");
}

/* Cancel whatever is being spoken right now (L2 audio, L1 browser voice). */
export function cancelSpeech() {
  try { stopTts(); } catch (_) {}
  if (typeof window !== "undefined" && window.speechSynthesis) {
    try { window.speechSynthesis.cancel(); } catch (_) {}
  }
  try {
    if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
      window.dispatchEvent(new CustomEvent("b2r:bargein"));
    }
  } catch (_) {}
}

/* createVoice({onFinal,onPartial,onError}) -> {start,stop,abort,markPhase,listening,destroy}
 * Extra optional options: `lang` (default zh-CN), `maxMs` hard stop.
 * `markPhase("thinking"|"speaking"|"listening"|"")` lets the caller feed the
 * §1.6 state machine its own phase (e.g. while the page answer is pending). */
export function createVoice(opts = {}) {
  const { onFinal, onPartial, onError, lang = "zh-CN", maxMs = 12000 } = opts;
  const Ctor = Rec();
  let rec = null, listening = false, lastGate = 0, stopTimer = null, destroyed = false;
  let phaseMark = "";

  // echo guard: never look at ASR input sooner than 600ms after the loudspeaker
  // started, nor sooner than 600ms after we cancelled something
  const gateMs = () => {
    const gate = lastGate ? Date.now() - lastGate : 1e9;
    const spoken = ttsSinceStart();
    return Math.min(gate, Number.isFinite(spoken) ? spoken : 1e9);
  };
  const phaseNow = () => phaseMark || (ttsSpeaking() ? "speaking" : (listening ? "listening" : "idle"));
  const fail = (e) => { if (typeof onError === "function") { try { onError(e); } catch (_) {} } };

  function start() {
    if (destroyed) return false;
    if (!Ctor) { fail(new Error(unsupportedReason())); return false; }
    if (listening) return true;
    rec = new Ctor();
    rec.lang = lang;
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 3;

    rec.onstart = () => { listening = true; phaseMark = ""; };

    rec.onresult = (ev) => {
      let interim = "", finalText = "";
      for (let i = ev.resultIndex || 0; i < ev.results.length; i++) {
        const res = ev.results[i];
        const txt = res[0] ? res[0].transcript : "";
        if (res.isFinal) finalText += txt; else interim += txt;
      }
      if (interim) {
        const text = interim.trim();
        const d = decide({ phase: phaseNow(), since_cancel_ms: gateMs(), partial: text },
                         { type: "partial", text, t_ms: Date.now() });
        if (d.decision === "cancel") {
          lastGate = Date.now();
          phaseMark = "";
          cancelSpeech();
        }
        if (typeof onPartial === "function") { try { onPartial(text, d); } catch (_) {} }
      }
      if (finalText.trim()) {
        const text = finalText.trim();
        const d = decide({ phase: phaseNow(), since_cancel_ms: gateMs(), partial: text },
                         { type: "final", text, t_ms: Date.now() });
        if (d.decision === "cancel") {          // 说完了但刚才被自己人声占着：先掐掉朗读
          lastGate = Date.now();
          phaseMark = "";
          cancelSpeech();
        }
        phaseMark = d.phase === "listening" ? "" : d.phase;
        if (d.decision === "listen" && typeof onFinal === "function") {
          try { onFinal(text, d); } catch (_) {}
        } else if (d.decision === "cancel" && typeof onPartial === "function") {
          try { onPartial(text, d); } catch (_) {}
        }
        try { rec.stop(); } catch (_) {}
      }
    };

    rec.onspeechend = () => { try { rec.stop(); } catch (_) {} };
    rec.onerror = (ev) => {
      listening = false;
      const code = (ev && ev.error) || "unknown";
      if (code === "no-speech" || code === "aborted") return;
      fail(new Error(code === "not-allowed"
        ? "麦克风权限被拒绝：请在地址栏允许麦克风"
        : `语音识别出错（${code}）`));
    };
    rec.onend = () => { listening = false; clearTimeout(stopTimer); stopTimer = null; };

    try { rec.start(); } catch (e) { listening = false; fail(e); return false; }
    lastGate = Date.now();
    clearTimeout(stopTimer);
    stopTimer = setTimeout(() => { try { rec.stop(); } catch (_) {} }, maxMs);
    return true;
  }

  function stop() {
    clearTimeout(stopTimer); stopTimer = null;
    if (rec) { try { rec.stop(); } catch (_) {} }
    listening = false;
    phaseMark = "";
  }

  function abort() {
    cancelSpeech();
    lastGate = Date.now();
    phaseMark = "";
    stop();
  }

  function destroy() {
    destroyed = true;
    stop();
    rec = null;
  }

  function markPhase(p) {
    phaseMark = PHASES.includes(p) ? p : "";
    return phaseMark;
  }

  return { start, stop, abort, markPhase, destroy, listening: () => listening, phase: phaseNow };
}

export default { createVoice, supported, unsupportedReason, cancelSpeech, decide, PHASES };
