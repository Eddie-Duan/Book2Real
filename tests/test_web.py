"""Front-end (no-build ESM) invariants: syntax, contract exports, honesty.

Everything here is checked against the real files in ``book2real/web/`` and, for
the pure functions, against a real ``node`` process — no browser, no npm, no
third-party package. Run it directly:

    python tests/test_web.py

The last line is ``N tests passed`` (same convention as the other test files).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "book2real" / "web"

NEW_MODULES = ["qa.js", "voice.js", "tts.js", "offline.js", "sw.js"]
NODE = shutil.which("node") or shutil.which("node.exe")


# --------------------------------------------------------------------- helpers
def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _node(args, cwd=ROOT, timeout=90):
    if not NODE:
        raise AssertionError("node not found on PATH — the front-end test needs node")
    return subprocess.run(
        [NODE, *args], cwd=str(cwd), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def _node_module_func(code: str, timeout=90):
    """Run an ES module snippet in node and decode its JSON stdout."""
    proc = _node(["--input-type=module", "-e", code], timeout=timeout)
    assert proc.returncode == 0, f"node failed: {proc.stderr[-1500:]}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"node printed nothing: {proc.stderr[-800:]}"
    return json.loads(lines[-1])


def _module_uri(name: str) -> str:
    return (WEB / name).resolve().as_uri()


def _exports(name: str) -> set[str]:
    src = _read(name)
    found = set(re.findall(r"^export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", src, re.M))
    found |= set(re.findall(r"^export\s+const\s+([A-Za-z_$][\w$]*)", src, re.M))
    found |= set(re.findall(r"^export\s+let\s+([A-Za-z_$][\w$]*)", src, re.M))
    return found


# ----------------------------------------------------------------------- files
def test_the_six_new_files_exist():
    for name in NEW_MODULES + ["manifest.json"]:
        path = WEB / name
        assert path.is_file(), f"missing {path}"
        assert path.stat().st_size > 300, f"{name} looks empty"


def test_every_new_js_file_passes_node_module_syntax_check():
    if not NODE:
        raise AssertionError("node not found on PATH")
    for name in NEW_MODULES:
        proc = subprocess.run(
            [NODE, "--input-type=module", "--check"],
            input=_read(name), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        assert proc.returncode == 0, f"{name} is not valid ESM: {proc.stderr[-800:]}"


def test_reader_js_is_still_a_classic_script_and_parses():
    html = _read("reader.html")
    assert '<script src="/static/reader.js"></script>' in html, "reader.js must stay a classic script"
    assert "type=\"module\"" not in html.split("<body>")[1].split("</html>")[0], \
        "reader.html must not switch reader.js to type=module (it shares globals)"
    proc = subprocess.run(
        [NODE, "--check"], input=_read("reader.js"), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert proc.returncode == 0, f"reader.js does not parse: {proc.stderr[-800:]}"


# -------------------------------------------------------------------- contract
def test_contract_exports_are_present():
    required = {
        "qa.js": {"ask", "renderAnswer", "openPanel"},
        "voice.js": {"createVoice", "supported", "cancelSpeech"},
        "tts.js": {"speakText", "stop", "splitSentences", "interpChars"},
        "offline.js": {"initOffline", "downloadBook", "verifyBook", "removeBook", "offlineStatus"},
    }
    for name, names in required.items():
        got = _exports(name)
        missing = names - got
        assert not missing, f"{name} is missing exports {sorted(missing)} (has {sorted(got)})"


def test_manifest_is_a_valid_standalone_web_app_manifest():
    data = json.loads(_read("manifest.json"))
    assert data["display"] == "standalone"
    assert data["start_url"] == "/"
    assert data["scope"] == "/"
    assert "纸书智伴" in data["name"]
    assert data["lang"] == "zh-CN"
    assert isinstance(data.get("icons"), list) and data["icons"]


def test_sw_has_a_single_cache_version_shared_with_offline_js():
    sw = _read("sw.js")
    versions = re.findall(r'CACHE_V\s*=\s*"([^"]+)"', sw)
    assert len(versions) == 1, f"sw.js must define CACHE_V once, found {versions}"
    assert versions[0] in _read("offline.js"), "offline.js must use the same cache version"
    assert "networkFirst" in sw and "cacheFirst" in sw
    assert 'request.mode === "navigate"' in sw, "navigation must be its own strategy"
    assert "/api/" in sw


def test_reader_js_keeps_every_existing_function():
    src = _read("reader.js")
    for fn in ["render", "renderArt", "renderStory", "showScene", "showSpot", "showWord",
               "speak", "go", "readAlong_", "runBeat", "stopBeats", "openStage", "closeAll",
               "loadNarration", "resolveSpots", "clauseAround", "wordSpan"]:
        assert re.search(rf"function\s+{re.escape(fn)}\s*\(", src), f"reader.js lost function {fn}()"
    # the beat pipeline must still be wired
    assert "playPageBeats" in src and "beatTimers" in src


def test_reader_js_only_adds_optional_hooks_in_the_five_places():
    src = _read("reader.js")
    # boot: offline is imported dynamically and never awaited for the page
    assert "loadMod(\"offline\")" in src
    # showSpot: stored text first, then the Q&A panel
    spot = src[src.index("function showSpot(s)"): src.index("/* ------------------------------------------------------------- word card")]
    assert "note.hidden = false" in spot and "qaOpen(" in spot
    assert spot.index("note.hidden = false") < spot.index("qaOpen("), \
        "showSpot must show the stored text before asking the backend"
    # blank art click: showScene kept, then the panel
    assert "showScene(page)" in src and "mode: \"spot\", box" in src
    # speak(): tts module first, legacy speechSynthesis body still there as L1
    speak = src[src.index("async function speak(text)"): src.index("function go(i)")]
    assert 'loadMod("tts")' in speak and "speechSynthesis.speak(u)" in speak
    # go(): flipping closes the panel
    go = src[src.index("function go(i) {"): src.index("$(\"#btnPrev\").onclick")]
    assert "qaClose()" in go
    # every dynamic import has a catch (no unhandled rejection)
    assert ".catch(() => null)" in src
    assert "import(`./${name}.js`).catch" in src


def test_reader_html_has_the_panel_offscreen_and_honest_controls():
    html = _read("reader.html")
    for el_id in ["qaPanel", "qaBody", "qaInput", "qaSend", "qaClose",
                  "btnAsk", "btnMic", "btnOffline", "offlineModal",
                  "offlineNote", "offlineDownload", "offlineStage"]:
        assert f'id="{el_id}"' in html, f"reader.html is missing #{el_id}"
    assert 'rel="manifest"' in html
    assert 'id="modalBackdrop"' in html, "the Q&A panel needs the shared backdrop to close on tap"


def test_new_controls_are_at_least_44px_and_use_the_existing_palette():
    css = _read("reader.css")
    for sel in ["#btnAsk", "#btnMic", "#btnOffline", ".qa-x"]:
        block = css[css.index(sel): css.index(sel) + 260]
        assert "44px" in block, f"{sel} needs a >=44px touch target"
    # no new colour literals beyond the five palette variables already at the top
    palette = {"--paper", "--ink", "--soft", "--accent", "--sage", "--line", "--shadow"}
    for var in palette:
        assert var in css
    qa_section = css[css.index("q&a panel"):]
    assert "#f6efe2" not in qa_section and "#7d8c6c" not in qa_section, \
        "new styles must use the existing variables, not a new palette"


def test_no_build_step_or_third_party_import_sneaks_in():
    for name in NEW_MODULES:
        src = _read(name)
        specs = []
        for line in src.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import", "export")):
                continue
            m = re.search(r'(?:from\s+)?["\']([^"\']+)["\']\s*;?\s*$', stripped)
            if m:
                specs.append(m.group(1))
        for spec in specs:
            assert spec.startswith("./") or spec.startswith("../"), \
                f"{name} imports {spec!r}: only relative browser-native ESM is allowed"
        assert "require(" not in src and "npm " not in src
    assert not (WEB / "package.json").exists(), "web/ must stay build-free"
    assert not (WEB / "node_modules").exists()
    assert not (WEB / "vendor").exists(), "no vendored third-party library in web/"


# ---------------------------------------------------------------- pure JS tests
def test_split_sentences_and_interp_chars_behave():
    code = f"""
const tts = await import({json.dumps(_module_uri("tts.js"))});
const out = {{
  empty: tts.splitSentences(""),
  two: tts.splitSentences("小满说，我们一起搭积木吧！黄米团跑来了。"),
  long: tts.splitSentences("啊".repeat(130)),
  interp: [tts.interpChars(10, 0, 2), tts.interpChars(10, 1, 2), tts.interpChars(10, 2, 2),
           tts.interpChars(10, 2.1, 2), tts.interpChars(0, 1, 2), tts.interpChars(10, 1, 0)],
}};
process.stdout.write(JSON.stringify(out));
"""
    out = _node_module_func(code)
    assert out["empty"] == []
    assert out["two"] == ["小满说，我们一起搭积木吧！", "黄米团跑来了。"]
    assert max(len(s) for s in out["long"]) == 60
    assert out["interp"] == [0, 5, 9, 9, 0, 0]


def test_voice_decide_matches_the_python_truth_source():
    try:
        sys.path.insert(0, str(ROOT))
        from book2real.voice_state import decide as py_decide
    except Exception:                                     # noqa: BLE001 - teammate may still be writing it
        py_decide = None

    phases = ["idle", "listening", "thinking", "speaking"]
    events = [
        {"type": "partial", "text": ""},
        {"type": "partial", "text": "我"},
        {"type": "partial", "text": " 好 的 "},
        {"type": "partial", "text": "这是什么"},
        {"type": "final", "text": "这是什么"},
        {"type": "final", "text": ""},
        {"type": "answer_ready", "text": "答案"},
        {"type": "speech_start", "text": ""},
        {"type": "speech_end", "text": ""},
        {"type": "unknown", "text": ""},
    ]
    sincs = [0, 300, 600, 601, 5000]
    cases = [{"phase": p, "since_cancel_ms": s} for p in phases for s in sincs]
    code = f"""
const voice = await import({json.dumps(_module_uri("voice.js"))});
const cases = {json.dumps(cases)};
const events = {json.dumps(events)};
const out = [];
for (const st of cases) for (const ev of events) {{
  const d = voice.decide(st, ev);
  out.push([st.phase, st.since_cancel_ms, ev.type, ev.text, d.decision, d.phase, d.chars]);
}}
process.stdout.write(JSON.stringify(out));
"""
    rows = _node_module_func(code)
    assert len(rows) == len(cases) * len(events)
    for phase, since, etype, text, decision, next_phase, chars in rows:
        assert decision in {"speak", "cancel", "wait", "listen"}
        assert next_phase in set(phases)
        if py_decide is not None:
            want = py_decide({"phase": phase, "since_cancel_ms": since}, {"type": etype, "text": text})
            assert decision == want["decision"], f"decision drift at {phase}/{since}/{etype}: {decision} != {want['decision']}"
            assert next_phase == want["phase"], f"phase drift at {phase}/{since}/{etype}"
            assert chars == want["chars"]

    # the three rules the contract spells out, independent of the python module
    by_key = {(p, s, t, tx): (d, np_) for p, s, t, tx, d, np_, _ in rows}
    assert by_key[("speaking", 601, "partial", " 好 的 ")][0] == "cancel"   # real barge-in
    assert by_key[("speaking", 600, "partial", " 好 的 ")][0] == "wait"     # 600ms echo guard
    assert by_key[("speaking", 601, "partial", "我")][0] == "wait"          # <2 chars
    assert by_key[("thinking", 0, "partial", "我")][0] == "cancel"          # talks over pending answer
    assert by_key[("idle", 0, "final", "这是什么")][0] == "listen"
    assert by_key[("listening", 0, "final", "这是什么")][0] == "listen"


def test_offline_status_is_honest_without_a_secure_context():
    code = f"""
const offline = await import({json.dumps(_module_uri("offline.js"))});
const st = offline.offlineStatus();
process.stdout.write(JSON.stringify({{ available: st.available, secure: st.secureContext,
  reason: st.reason, cacheVersion: st.cacheVersion, cacheName: st.cacheName }}));
"""
    out = _node_module_func(code)
    assert out["available"] is False, "node is not a secure context: available must be false"
    assert "离线缓存不可用" in out["reason"] and "localhost" in out["reason"]
    assert out["cacheVersion"] == "b2r-v1"
    assert out["cacheName"] == "b2r-cache-b2r-v1"


def test_qa_ask_never_throws_without_a_backend():
    # no fetch() at all in node -> ask() must resolve to the honest degraded answer
    code = f"""
const qa = await import({json.dumps(_module_uri("qa.js"))});
const r = await qa.ask({{ book: "cn01", page: "p03", mode: "free", query: "这是什么" }});
process.stdout.write(JSON.stringify({{ answer: r.answer, fallback: r.fallback, source: r.source,
  citations: r.citations, page: r.page, socratic: r.socratic }}));
"""
    out = _node_module_func(code)
    assert out["answer"] == "这一页的问答服务没打开"
    assert out["fallback"] == "qa_unavailable"
    assert out["citations"] == []
    assert out["page"] == "p03"


if __name__ == "__main__":
    ok = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print(f"ok  {name}")
    print(f"{ok} tests passed")
