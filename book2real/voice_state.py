"""Interruption state machine for the reader's voice layer.

Pure functions only: no I/O, no globals, no mutation of the arguments.  This
module is the single tested source of truth for the rules in
``docs/INTEGRATION_CONTRACT.md`` §1.6; ``web/voice.js`` mirrors it 1:1.

    decide(state, event) -> {"decision", "phase", "text", "chars", "reason"}

``decision`` is what the caller should do next:

    speak   start TTS playback (``text`` is what to say)
    cancel  stop the current speech / abandon the pending answer
    wait    no action this tick
    listen  treat ``text`` as the user's query and (re)arm the ASR

``phase`` is the phase the caller should record afterwards.

state:  {"phase": "idle|listening|thinking|speaking", "since_cancel_ms": int,
         "partial": str}
event:  {"type": "partial|final|speech_start|speech_end|answer_ready",
         "text": str, "t_ms": int}

Mandated rules (contract §1.6):

* speaking + ``partial`` whose whitespace-free text is >= 2 chars and
  ``since_cancel_ms > 600`` -> ``cancel`` (a real barge-in; the 600 ms window is
  the echo guard so our own TTS tail is not mistaken for the user).
* thinking + ``partial`` -> ``cancel`` (the user talks over the pending answer).
* idle/listening + ``final`` -> ``listen`` (commit the transcript as a query).

The remaining transitions are conservative defaults (``wait``) so that a
divergence from the JS side can never make the reader talk over the user.
"""
from __future__ import annotations

PHASES = ("idle", "listening", "thinking", "speaking")
EVENT_TYPES = ("partial", "final", "speech_start", "speech_end", "answer_ready")
DECISIONS = ("speak", "cancel", "wait", "listen")

#: Minimum whitespace-free length before a partial may interrupt speech.
MIN_INTERRUPT_CHARS = 2
#: Echo guard: for this long after a cancel/stop, ASR input is treated as the
#: tail of our own audio, never as speech of the user.
ECHO_GUARD_MS = 600


def _clean(text) -> str:
    """Drop every whitespace char so " 好 的 " counts as 2 characters."""
    return "".join(str(text if text is not None else "").split())


def _result(decision: str, phase: str, text: str = "", reason: str = "") -> dict:
    return {"decision": decision, "phase": phase,
            "text": text, "chars": len(_clean(text)), "reason": reason}


def decide(state: dict, event: dict) -> dict:
    """Pure: returns the next decision + phase, never mutates its inputs."""
    st = state if isinstance(state, dict) else {}
    ev = event if isinstance(event, dict) else {}

    phase = str(st.get("phase") or "idle")
    if phase not in PHASES:
        phase = "idle"
    try:
        since_cancel_ms = int(st.get("since_cancel_ms") or 0)
    except (TypeError, ValueError):
        since_cancel_ms = 0
    if since_cancel_ms < 0:
        since_cancel_ms = 0

    etype = str(ev.get("type") or "")
    text = str(ev.get("text") or "").strip()
    clean = _clean(text)
    long_enough = len(clean) >= MIN_INTERRUPT_CHARS
    echo_guarded = since_cancel_ms <= ECHO_GUARD_MS

    if etype == "partial":
        if phase == "speaking":
            if not long_enough:
                return _result("wait", "speaking", text, "too_short")
            if echo_guarded:
                return _result("wait", "speaking", text, "echo_guard")
            return _result("cancel", "listening", clean, "barge_in")
        if phase == "thinking":
            return _result("cancel", "listening", clean, "barge_in_thinking")
        # idle / listening: the utterance is still growing, final commits it.
        return _result("wait", phase, text, "partial")

    if etype == "final":
        if phase == "speaking":
            if not long_enough:
                return _result("wait", "speaking", text, "too_short")
            if echo_guarded:
                return _result("wait", "speaking", text, "echo_guard")
            return _result("cancel", "listening", text, "barge_in_final")
        if phase == "thinking":
            return _result("wait", "thinking", text, "already_thinking")
        return _result("listen", "listening", text, "commit")

    if etype == "answer_ready":
        if phase in ("idle", "thinking"):
            return _result("speak", "speaking", text, "answer")
        if phase == "listening":
            return _result("wait", "listening", text, "user_speaking")
        return _result("wait", "speaking", text, "already_speaking")

    if etype == "speech_start":
        if phase == "speaking":
            return _result("wait", "speaking", text, "already_speaking")
        if phase == "listening":
            return _result("wait", "listening", text, "user_speaking")
        return _result("speak", "speaking", text, "speech_start")

    if etype == "speech_end":
        if phase == "speaking":
            return _result("listen", "listening", text, "speech_done")
        return _result("wait", phase, text, "speech_end")

    return _result("wait", phase, text, "unknown_event")
