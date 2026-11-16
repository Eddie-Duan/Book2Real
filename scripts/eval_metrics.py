#!/usr/bin/env python
"""实测指标：`python scripts/eval_metrics.py`

产出：
  runs/eval/metrics.json   原始数据（每项含 n / 环境 / 时间戳 / 原始样本）
  docs/METRICS.md          表格（自动生成，不要手改）

5 项（依据 docs/INTEGRATION_CONTRACT.md §4.2）：
  1 页面覆盖率（books/*/book.json 源页 vs reader 屏，扣 curation.skip）
  2 /api/tts 二次命中首字节（ms，连续 10 次取中位/最大）
  3 /api/ask 规则模式首响 P50/P95（30 次）
  4 前端静态资源总字节（走 HTTP 实测 Content-Length）
  5 /api/ask 引用可回溯率（quote 是否为该页原文子串）

只写实测；依赖没落地时写"未测（原因）"，**不把赛题目标值当实测**。只用标准库。
"""
from __future__ import annotations

import http.client
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402

REPO = gate.REPO
OUT_JSON = REPO / "runs" / "eval" / "metrics.json"
OUT_MD = REPO / "docs" / "METRICS.md"

SERVICE_METRICS = (
    "tts_first_byte",
    "ask_latency",
    "front_bytes",
    "citation_trace",
)

# 赛题目标值：单独一列，明确标注"非实测"与来源，绝不与实测值混写。
# 逐项对照 docs/赛题分析.md §6「评测」表（行号见注释）。
TARGETS = {
    # §6: 页面覆盖率 | 100%
    "coverage": "100%（docs/赛题分析.md §6）",
    # §6 只有"语音首响 P95 ≤1.5 s"，没有单列 TTS 缓存命中首字节
    "tts_first_byte": "—（赛题未单列；相关：语音首响 P95 ≤1.5 s，§6）",
    # §6: 语音/点击首响 P95 ≤1.5 s / ≤1 s；§4.2 点击首响目标 ≤1 s
    "ask_latency": "点击首响 P95 ≤1 s（docs/赛题分析.md §6、§4.2）",
    # §6 没有前端资源字节数目标
    "front_bytes": "—（赛题未单列）",
    # §6 没有引用可回溯率目标；相关项是幻觉率 ≤5%
    "citation_trace": "—（赛题未单列；相关：幻觉率 ≤5%，§6）",
}


# --------------------------------------------------------------------------- 工具
def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _env_info() -> dict:
    cpu = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "") or "unknown"
    node = ""
    exe = shutil.which("node")
    if exe:
        try:
            p = subprocess.run([exe, "--version"], capture_output=True, timeout=20)
            node = p.stdout.decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            node = ""
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu": cpu,
        "gpu": "未使用（本机纯 CPU，无 GPU 参与）",
        "python": platform.python_version(),
        "node": node,
        "runner": "scripts/eval_metrics.py",
    }


ENV = _env_info()
ENV_SHORT = f"{platform.system()} {platform.release()} / 纯 CPU / Py{platform.python_version()}"


def metric(mid: str, name: str, value: str, n, note: str = "", measured: bool = True,
           raw=None) -> dict:
    return {"id": mid, "name": name, "value": value, "n": n, "note": note,
            "measured": measured, "raw": raw, "timestamp": _now(), "env": ENV_SHORT}


def unmeasured(mid: str, name: str, reason: str, note: str = "") -> dict:
    return metric(mid, name, f"未测（{reason}）", 0,
                  note=note, measured=False)
def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _ttfb(port: int, path: str, timeout: float = 60.0) -> tuple[int, float, dict]:
    """真正的首字节耗时：从发请求到拿到第一个 body byte。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        t0 = time.perf_counter()
        conn.request("GET", path)
        r = conn.getresponse()
        r.read(1)
        dt = (time.perf_counter() - t0) * 1000.0
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        return r.status, dt, hdrs
    finally:
        conn.close()


def _ask(port: int, payload: dict, timeout: float = 60.0) -> tuple[int, float, bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    t0 = time.perf_counter()
    st, _, raw = gate.http_req(port, "/api/ask", method="POST", body=body,
                               headers={"Content-Type": "application/json"}, timeout=timeout)
    return st, (time.perf_counter() - t0) * 1000.0, raw


def _pct(values: list[float], p: float) -> float:
    s = sorted(values)
    k = max(0, math.ceil(p / 100.0 * len(s)) - 1)
    return s[k]


# --------------------------------------------------------------------------- 1 覆盖率
def metric_coverage() -> dict:
    rep = gate.coverage_report(REPO / "books")
    t = rep["totals"]
    expected, covered = t["expected"], t["covered"]
    if expected == 0:
        return unmeasured("coverage", "页面覆盖率",
                          "没有可比的 reader 包（books/*/reader/book.json 均不存在）")
    pct = 100.0 * covered / expected
    detail = "；".join(
        f"{b['id']} {b['covered']}/{b['expected']}" for b in rep["books"] if b.get("has_reader"))
    comparable = sum(1 for b in rep["books"] if b.get("has_reader"))
    note = f"{detail}；reader 共 {t['screens']} 屏"
    if t.get("missing_reader"):
        note += f"；另有 {t['missing_reader']} 本无 reader 包，未参与比对"
    return metric("coverage", "页面覆盖率", f"{covered}/{expected} 源页 = {pct:.1f}%",
                  n=f"{comparable} 本书", note=note,
                  raw={"per_book": [b for b in rep["books"]]})


# --------------------------------------------------------------------------- 2 TTS 首字节
TTS_TEXT = "你好，我是纸书智伴。"


def metric_tts(port: int) -> dict:
    mid, name = "tts_first_byte", "/api/tts 二次命中首字节"
    path = f"/api/tts?text={quote(TTS_TEXT, safe='')}"
    try:
        st, dt_warm, hdr = _ttfb(port, path, timeout=120.0)
    except Exception as exc:  # noqa: BLE001
        return unmeasured(mid, name, f"/api/tts 请求异常 {type(exc).__name__}: {exc}",
                          note="reader 服务本身可用，但 TTS 端点请求失败")
    if st == 404:
        return unmeasured(mid, name, "依赖未就绪：/api/tts 路由不存在",
                          note="等 voice-agent 的 /api/tts 落地")
    if st != 200:
        return unmeasured(mid, name, f"TTS 首次合成返回 HTTP {st}（本机 edge-tts 未安装时依赖 SAPI 回落）",
                          note=f"warm-up 请求耗时 {dt_warm:.0f} ms，body 无音频；/api/tts 契约见 §1.1")
    samples: list[float] = []
    hits = misses = errors = 0
    for _ in range(10):
        try:
            st2, dt, h2 = _ttfb(port, path, timeout=60.0)
        except Exception:  # noqa: BLE001
            errors += 1
            continue
        if st2 != 200:
            errors += 1
            continue
        samples.append(dt)
        cache = (h2.get("x-tts-cache") or "").lower()
        hits += cache == "hit"
        misses += cache == "miss"
    if not samples:
        return unmeasured(mid, name, "10 次命中请求全部失败", note=f"warm-up={dt_warm:.0f} ms, errors={errors}")
    med = statistics.median(samples)
    note = (f"首次请求 {dt_warm:.0f} ms 后，同一文本连续 10 次命中；"
            f"X-TTS-Cache hit={hits} / miss={misses} / 失败={errors}")
    return metric(mid, name, f"中位 {med:.1f} ms / 最大 {max(samples):.1f} ms", n=len(samples),
                  note=note, raw={"samples_ms": [round(v, 1) for v in samples],
                                  "warmup_ms": round(dt_warm, 1)})


# --------------------------------------------------------------------------- 3 /api/ask 首响
ASK_PAYLOAD = {"book": "cn01", "page": "p03", "mode": "free",
               "query": "小满和花花在做什么？", "history": []}


def metric_ask(port: int) -> dict:
    mid, name = "ask_latency", "/api/ask 规则模式首响 P50/P95"
    try:
        st, warm, raw = _ask(port, ASK_PAYLOAD)
    except Exception as exc:  # noqa: BLE001
        return unmeasured(mid, name, f"/api/ask 请求异常 {type(exc).__name__}: {exc}")
    if st == 404:
        return unmeasured(mid, name, "依赖未就绪：/api/ask 路由不存在",
                          note="等 voice-agent 的 /api/ask 落地")
    data = _json(raw) if st == 200 else None
    if st != 200:
        return unmeasured(mid, name, f"/api/ask 返回 HTTP {st}")
    if isinstance(data, dict) and data.get("fallback") == "qa_unavailable":
        return unmeasured(mid, name, "qa-agent 未就绪（/api/ask 返回 qa_unavailable 兜底）",
                          note=f"warm-up {warm:.1f} ms；该响应是兜底文案，不是真实规则问答")
    samples: list[float] = []
    errors = 0
    for _ in range(30):
        try:
            st2, dt, _raw = _ask(port, ASK_PAYLOAD)
        except Exception:  # noqa: BLE001
            errors += 1
            continue
        if st2 != 200:
            errors += 1
            continue
        samples.append(dt)
    if not samples:
        return unmeasured(mid, name, "30 次请求全部失败")
    note = (f"规则模式（source={(data or {}).get('source')!r}, fallback={(data or {}).get('fallback')!r}）；"
            f"30 次单请求含 HTTP 往返，失败 {errors} 次；warm-up {warm:.1f} ms")
    return metric(mid, name, f"P50 {_pct(samples, 50):.1f} ms / P95 {_pct(samples, 95):.1f} ms",
                  n=len(samples), note=note,
                  raw={"samples_ms": [round(v, 1) for v in samples]})


# --------------------------------------------------------------------------- 4 前端字节
FRONT_FILES = (
    ("/", "reader.html"),
    ("/static/reader.css", "reader.css"),
    ("/static/reader.js", "reader.js"),
    ("/static/qa.js", "qa.js"),
    ("/static/voice.js", "voice.js"),
    ("/static/tts.js", "tts.js"),
    ("/static/offline.js", "offline.js"),
    ("/static/sw.js", "sw.js"),
    ("/static/manifest.json", "manifest.json"),
)


def metric_front(port: int) -> dict:
    mid, name = "front_bytes", "前端静态资源总字节"
    total = 0
    got: list[dict] = []
    missing: list[str] = []
    for path, fname in FRONT_FILES:
        try:
            st, _, body = gate.http_req(port, path, timeout=30.0)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{fname}(请求异常 {type(exc).__name__})")
            continue
        if st == 200:
            total += len(body)
            got.append({"file": fname, "path": path, "bytes": len(body)})
        else:
            missing.append(f"{fname}({st})")
    if not got:
        return unmeasured(mid, name, "9 个前端资源全部取不到（服务不可用？）")
    note = ("全部 200：" + ", ".join(g["file"] for g in got)) if not missing \
        else f"缺 {len(missing)} 个未计入总量：{', '.join(missing)}"
    return metric(mid, name, f"{total} bytes（{len(got)}/{len(FRONT_FILES)} 个资源）",
                  n=len(got), note=note, raw={"files": got, "missing": missing})


# --------------------------------------------------------------------------- 5 引用可回溯
def _pick_book() -> tuple[str, dict]:
    """优先 cn01；否则第一本有 reader 包的书。"""
    books = sorted((REPO / "books").glob("*/reader/book.json"))
    if not books:
        return "", {}
    preferred = REPO / "books" / "cn01" / "reader" / "book.json"
    path = preferred if preferred.is_file() else books[0]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "", {}
    return str(data.get("book_id") or path.parents[1].name), data


def _page_corpus(pg: dict, characters: dict[str, dict]) -> str:
    parts = [pg.get("text") or ""]
    for seg in pg.get("segments") or []:
        if isinstance(seg, str):
            parts.append(seg)
        elif isinstance(seg, dict):
            parts.append(seg.get("text") or "")
    parts.append(pg.get("scene") or "")
    for sp in pg.get("spots") or []:
        if isinstance(sp, dict):
            parts.append(sp.get("desc") or "")
    for cid in pg.get("characters") or []:
        ch = characters.get(cid) or {}
        parts.extend([str(ch.get("name") or ""), str(ch.get("role") or ""), str(ch.get("desc") or "")])
    return "\n".join(p for p in parts if p)


def metric_citations(port: int) -> dict:
    mid, name = "citation_trace", "/api/ask 引用可回溯率"
    book_id, data = _pick_book()
    if not data:
        return unmeasured(mid, name, "找不到可用的 reader/book.json")
    chars = {str(c.get("id")): c for c in (data.get("characters") or []) if isinstance(c, dict)}
    pages = [pg for pg in (data.get("pages") or [])
             if isinstance(pg, dict) and len(str(pg.get("text") or "")) >= 8]
    pages = pages[:12]
    if not pages:
        return unmeasured(mid, name, f"{book_id} 没有含正文的页")
    responses = 0
    total_quotes = verified = 0
    fallbacks: dict[str, int] = {}
    bad_examples: list[str] = []
    for pg in pages:
        text = str(pg.get("text") or "")
        clause = text.split("，")[0].split("。")[0][:10] or "这一页讲了什么？"
        payload = {"book": book_id, "page": pg.get("id"), "mode": "free",
                   "query": clause, "history": []}
        try:
            st, _dt, raw = _ask(port, payload)
        except Exception:  # noqa: BLE001
            continue
        resp = _json(raw) if st == 200 else None
        if not isinstance(resp, dict):
            continue
        responses += 1
        fb = resp.get("fallback")
        fallbacks[str(fb)] = fallbacks.get(str(fb), 0) + 1
        corpus = _page_corpus(pg, chars)
        for cite in resp.get("citations") or []:
            if not isinstance(cite, dict):
                continue
            quote_text = str(cite.get("quote") or "")
            total_quotes += 1
            if quote_text and quote_text in corpus:
                verified += 1
            elif len(bad_examples) < 2:
                bad_examples.append(quote_text[:40])
    if responses == 0:
        return unmeasured(mid, name, "/api/ask 12 次抽样全部失败")
    if total_quotes == 0:
        return unmeasured(mid, name,
                          f"抽样 {responses} 个页面未产生 citations（fallback 分布 {fallbacks}）",
                          note="可能 qa 未就绪或规则检索全部 no_evidence")
    ratio = 100.0 * verified / total_quotes
    note = (f"抽样 {responses} 页/问（{book_id}），citations 共 {total_quotes} 条；"
            f"fallback 分布 {fallbacks}")
    if bad_examples:
        note += f"；不可回溯示例：{bad_examples}"
    return metric(mid, name, f"{verified}/{total_quotes} 条 quote 为原文子串 = {ratio:.1f}%",
                  n=total_quotes, note=note,
                  raw={"responses": responses, "fallbacks": fallbacks,
                       "bad_examples": bad_examples})


# --------------------------------------------------------------------------- 输出
def write_markdown(metrics: list[dict], ts: str) -> None:
    lines = [
        "# 实测指标（自动生成，勿手改）",
        "",
        f"> 生成命令：`python scripts/eval_metrics.py` · 生成时间：{ts}",
        "> 原始数据：`runs/eval/metrics.json`",
        "> 「实测值」列只写本次真跑出来的数字（未测写「未测（原因）」）；"
        "「目标」列是单独标注的**非实测**赛题目标（来源 `docs/赛题分析.md`），两列不混写。",
        "",
        "## 环境",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 操作系统 | {ENV['os']} |",
        f"| 机器/架构 | {ENV['machine']} |",
        f"| CPU | {ENV['cpu']} |",
        f"| GPU | {ENV['gpu']} |",
        f"| Python | {ENV['python']} |",
        f"| Node | {ENV['node'] or '未安装'} |",
        "",
        "## 指标",
        "",
        "| # | 指标 | 实测值（本次） | 目标（非实测，来源 docs/赛题分析.md） | n | 环境 | 时间戳 | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, m in enumerate(metrics, start=1):
        note = (m.get("note") or "").replace("|", "\\|").replace("\n", " ")
        target = TARGETS.get(m["id"], "—").replace("|", "\\|")
        lines.append(
            f"| {i} | {m['name']} | {m['value']} | {target} | {m['n']} | {m['env']} | "
            f"{m['timestamp']} | {note} |")
    lines += [
        "",
        "## 说明",
        "",
        "- 「实测值」只写这次脚本真跑出来的数字；没跑到的写「未测（原因）」，"
        "不是 0，也不是目标值。",
        "- 「目标」列**不是实测**：逐条抄自 `docs/赛题分析.md` §6 的赛题目标，"
        "标「—」表示赛题没有给该指标目标；标「相关」只是列了相近的赛题目标供对照，"
        "不等价于本行指标。",
        "- 覆盖率按 `source_page` 精确比对，扣掉 `curation.json` 里 `skip:true` 的源页；"
        "拆成多屏的源页算覆盖 1 页（屏数另计）。",
        "- 「首字节」用原始 socket 计时：发请求 → 收到第一个 body byte；含 HTTP 往返。",
        "",
    ]
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    print(f"实测指标 · repo={REPO}")
    metrics: list[dict] = [metric_coverage()]
    print(f"  [1/5] {metrics[0]['name']}: {metrics[0]['value']}")

    results: dict[str, dict] = {}
    try:
        with gate.ReaderServer() as srv:
            port = srv.port
            results["tts_first_byte"] = metric_tts(port)
            print(f"  [2/5] {results['tts_first_byte']['name']}: {results['tts_first_byte']['value']}")
            results["ask_latency"] = metric_ask(port)
            print(f"  [3/5] {results['ask_latency']['name']}: {results['ask_latency']['value']}")
            results["front_bytes"] = metric_front(port)
            print(f"  [4/5] {results['front_bytes']['name']}: {results['front_bytes']['value']}")
            results["citation_trace"] = metric_citations(port)
            print(f"  [5/5] {results['citation_trace']['name']}: {results['citation_trace']['value']}")
    except Exception as exc:  # noqa: BLE001 - 服务起不来也要产出报告
        reason = f"reader 服务不可用：{type(exc).__name__}: {exc}"
        names = {"tts_first_byte": "/api/tts 二次命中首字节",
                 "ask_latency": "/api/ask 规则模式首响 P50/P95",
                 "front_bytes": "前端静态资源总字节",
                 "citation_trace": "/api/ask 引用可回溯率"}
        for key in SERVICE_METRICS:
            results.setdefault(key, unmeasured(key, names[key], reason))

    for key in SERVICE_METRICS:
        metrics.append(results[key])

    ts = _now()
    payload = {"generated_at": ts, "env": ENV,
               "script": "scripts/eval_metrics.py", "metrics": metrics}
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(metrics, ts)
    measured = sum(1 for m in metrics if m["measured"])
    print(f"  已写 {OUT_MD.relative_to(REPO)} / {OUT_JSON.relative_to(REPO)}")
    print(f"实测 {measured}/{len(metrics)} 项，未测 {len(metrics) - measured} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
