#!/usr/bin/env python
"""集成门：`python scripts/gate.py [--strict]`

五门（依据 docs/INTEGRATION_CONTRACT.md §4.1）：
  1 语法门  python -m compileall -q book2real scripts
  2 测试门  逐个 python tests/test_*.py（不依赖 pytest），末行必须 `N tests passed`
  3 服务门  临时起 reader 到空闲端口，探 CSP/nosniff/Referrer-Policy、/api/books、
            /api/tts?probe=1、/api/books/<id>/files、/static/sw.js、
            路径穿越 403/404；探完 finally 关掉子进程并验端口已释放
  4 覆盖门  每本 books/<id>/book.json 的源页 vs reader/book.json 的屏（按 source_page
            与 curation.skip 精确比对），缺页/多页/分屏标记不一致都红并点名
  5 前端门  web/ 下 6 个模块存在 + node --input-type=module --check + manifest.json 合法

末行固定 `结果：N 通过，M 失败`；有失败 exit 1，--strict 时警告也算失败。
只用标准库；不依赖 pytest。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable

RETRIES = 3           # 并行写入容忍：最多 3 次
RETRY_DELAY = 5.0     # 每次退避 5s

FRONT_MODULES = ("qa.js", "voice.js", "tts.js", "offline.js", "sw.js", "manifest.json")
JS_MODULES = tuple(m for m in FRONT_MODULES if m.endswith(".js"))
TEST_FOOTER = re.compile(r"(\d+)\s+tests passed")

OK, FAIL, PENDING = "ok", "fail", "pending"


# --------------------------------------------------------------------------- 通用
@dataclass
class Result:
    name: str
    ok: bool = True
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def add(self, msg: str) -> None:
        self.lines.append(msg)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.lines.append(f"失败  {msg}")

    def pending(self, msg: str) -> None:
        self.ok = False
        self.lines.append(f"依赖未就绪  {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 600,
         stdin_path: str | None = None) -> tuple[int, str, str]:
    """跑子进程，返回 (exit, stdout, stderr)。stdin 可来自文件（node --check 用）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    fin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd or REPO), stdin=fin,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, env=env,
        )
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    finally:
        if stdin_path:
            fin.close()


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def port_open(port: int, timeout: float = 0.3) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_req(port: int, path: str, method: str = "GET", body: bytes | None = None,
             headers: dict | None = None, timeout: float = 20.0) -> tuple[int, dict, bytes]:
    """发一个原始 HTTP 请求；path 原样发送（路径穿越测试需要）。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        data = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        return r.status, hdrs, data
    finally:
        conn.close()


def _json_or_none(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 - 探测用，坏 JSON 只是失败证据
        return None


def _short(text: str, n: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


class ReaderServer:
    """在空闲端口起 `python -m book2real reader --port N`，退出时一定关掉。

    用完检查 `port_still_open`：为 True 说明子进程没死干净（留给门判红）。
    """

    def __init__(self, repo: Path | None = None, port: int | None = None):
        self.repo = repo or REPO
        self.port = port or find_free_port()
        self.proc: subprocess.Popen | None = None
        self.log_tail = ""
        self.port_still_open = False
        self._tmpdir: Path | None = None
        self._logf = None

    def __enter__(self) -> "ReaderServer":
        self._tmpdir = Path(tempfile.mkdtemp(prefix="b2r-gate-"))
        log_path = self._tmpdir / "reader.log"
        self._logf = open(log_path, "wb")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [PY, "-m", "book2real", "reader", "--port", str(self.port)],
            cwd=str(self.repo), stdout=self._logf, stderr=self._logf, creationflags=flags,
        )
        deadline = time.time() + 30.0
        try:
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError(
                        f"reader 进程提前退出 exit={self.proc.returncode}：{self._read_log()}")
                try:
                    http_req(self.port, "/", timeout=2.0)
                    return self
                except (OSError, http.client.HTTPException):
                    time.sleep(0.25)
            raise RuntimeError(f"reader 30s 内未监听 {self.port}：{self._read_log()}")
        except Exception:
            self._shutdown()
            self._cleanup()
            raise

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._shutdown()
        self.log_tail = self._read_log()
        self._cleanup()
        return False

    def _read_log(self) -> str:
        try:
            if self._logf:
                self._logf.flush()
                return self._tmpdir.joinpath("reader.log").read_text(encoding="utf-8", errors="replace")[-1200:]
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _shutdown(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not port_open(self.port):
                break
            time.sleep(0.1)
        self.port_still_open = port_open(self.port)

    def _cleanup(self) -> None:
        if self._logf:
            try:
                self._logf.close()
            except Exception:  # noqa: BLE001
                pass
            self._logf = None
        if self._tmpdir and self._tmpdir.exists():
            shutil.rmtree(self._tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- 1 语法门
def _syntax_once() -> Result:
    res = Result("语法门")
    try:
        code, out, err = _run([PY, "-m", "compileall", "-q", "book2real", "scripts"], timeout=240)
    except subprocess.TimeoutExpired:
        res.fail("compileall 超时（240s）")
        return res
    if code == 0:
        res.add("ok  python -m compileall -q book2real scripts")
        return res
    bad = [ln.strip() for ln in (out + "\n" + err).splitlines() if ln.strip()]
    res.fail("compileall 有错误：" + " | ".join(bad[:5]))
    return res


def check_syntax(retries: int = RETRIES, delay: float = RETRY_DELAY) -> Result:
    return _retry_until_ok(_syntax_once, retries, delay)


# --------------------------------------------------------------------------- 2 测试门
_TEST_HARNESS = r'''
import importlib.util, sys, traceback
from pathlib import Path

path = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(path.parents[1]))
spec = importlib.util.spec_from_file_location("b2r_test_under_gate", str(path))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
names = sorted(n for n in dir(mod) if n.startswith("test_") and callable(getattr(mod, n)))
if not names:
    print("0 collected tests")
    sys.exit(2)
fails = 0
for name in names:
    try:
        getattr(mod, name)()
        print(f"ok  {name}")
    except Exception as exc:  # noqa: BLE001
        fails += 1
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        print(traceback.format_exc())
if fails:
    print(f"{fails} tests failed")
    sys.exit(1)
print(f"{len(names)} tests passed")
'''


def _write_harness() -> tuple[Path, Path]:
    d = Path(tempfile.mkdtemp(prefix="b2r-gate-tests-"))
    p = d / "run_tests.py"
    p.write_text(_TEST_HARNESS, encoding="utf-8")
    return d, p


def check_tests() -> Result:
    """逐个跑 tests/test_*.py；没有自跑入口的 pytest-only 文件用 stdlib 收集器补跑。"""
    res = Result("测试门")
    files = sorted((REPO / "tests").glob("test_*.py"))
    if not files:
        res.fail("tests/ 下没有 test_*.py")
        return res
    tmp_dirs: list[Path] = []
    harness: Path | None = None
    total = 0
    try:
        for f in files:
            try:
                code, out, err = _run([PY, str(f)], timeout=600)
            except subprocess.TimeoutExpired:
                res.fail(f"{f.name}: 超时（600s）")
                continue
            out_lines = [ln for ln in out.splitlines() if ln.strip()]
            last = out_lines[-1] if out_lines else ""
            m = TEST_FOOTER.search(last)
            traceback_seen = "Traceback" in err or ("Traceback" in out and not m)
            if code == 0 and m and not traceback_seen:
                total += int(m.group(1))
                res.add(f"ok  {f.name}: {m.group(1)} tests passed")
                continue
            # pytest-only 文件（没有 __main__ 自跑入口）→ stdlib 收集器真跑
            if code == 0 and not m and not traceback_seen:
                if harness is None:
                    d, harness = _write_harness()
                    tmp_dirs.append(d)
                try:
                    code2, out2, err2 = _run([PY, str(harness), str(f)], timeout=600)
                except subprocess.TimeoutExpired:
                    res.fail(f"{f.name}: 收集器超时（600s）")
                    continue
                out2_lines = [ln for ln in out2.splitlines() if ln.strip()]
                last2 = out2_lines[-1] if out2_lines else ""
                m2 = TEST_FOOTER.search(last2)
                if code2 == 0 and m2 and "Traceback" not in err2:
                    total += int(m2.group(1))
                    res.add(f"ok  {f.name}: {m2.group(1)} tests passed（该文件无自跑入口，gate 内置 stdlib 收集器执行）")
                else:
                    detail = _short(" | ".join(out2_lines[-3:] + ([err2.strip().splitlines()[-1]] if err2.strip() else [])))
                    res.fail(f"{f.name}: 无 `N tests passed` 末行 / 有用例失败（exit={code2}）{detail}")
                continue
            detail = _short(" | ".join(out_lines[-2:] + ([last] if last else []) + ([err.strip().splitlines()[-1]] if err.strip() else [])))
            res.fail(f"{f.name}: exit={code}，末行={last!r}，Traceback={'有' if traceback_seen else '无'} {detail}")
    finally:
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)
    if res.ok:
        res.add(f"合计 {total} 条用例通过（{len(files)} 个文件）")
    return res


# --------------------------------------------------------------------------- 3 服务门
def _check_headers(port: int, checks: list[dict]) -> None:
    def rec(name: str, ok: bool, msg: str = "", kind: str = FAIL) -> None:
        checks.append({"name": name, "ok": ok, "msg": msg, "kind": kind})

    try:
        st, h, _ = http_req(port, "/")
    except Exception as exc:  # noqa: BLE001
        rec("GET /", False, f"请求失败 {type(exc).__name__}: {exc}")
        return
    if st != 200:
        rec("GET /", False, f"HTTP {st}（期望 200）")
        return
    csp = h.get("content-security-policy", "")
    if not csp:
        rec("GET / CSP", False, "缺 Content-Security-Policy 头（契约 §1.5，owner voice-agent）", PENDING)
    elif "default-src 'self'" not in csp:
        rec("GET / CSP", False, f"CSP 不含 default-src 'self'：{_short(csp, 90)}")
    else:
        rec("GET / CSP", True, "有 Content-Security-Policy")
    nosniff = h.get("x-content-type-options", "").lower()
    if nosniff != "nosniff":
        rec("GET / nosniff", False, f"X-Content-Type-Options={nosniff!r}（期望 nosniff，契约 §1.5）", PENDING)
    else:
        rec("GET / nosniff", True, "X-Content-Type-Options: nosniff")
    ref = h.get("referrer-policy", "").lower()
    if ref != "no-referrer":
        rec("GET / Referrer-Policy", False, f"Referrer-Policy={ref!r}（期望 no-referrer，契约 §1.5）", PENDING)
    else:
        rec("GET / Referrer-Policy", True, "Referrer-Policy: no-referrer")


def _check_books(port: int, checks: list[dict]) -> str:
    def rec(name: str, ok: bool, msg: str = "", kind: str = FAIL) -> None:
        checks.append({"name": name, "ok": ok, "msg": msg, "kind": kind})

    try:
        st, _, raw = http_req(port, "/api/books")
    except Exception as exc:  # noqa: BLE001
        rec("GET /api/books", False, f"请求失败 {type(exc).__name__}: {exc}")
        return ""
    data = _json_or_none(raw) if st == 200 else None
    books = (data or {}).get("books") if isinstance(data, dict) else None
    if st != 200 or not isinstance(books, list) or not books:
        rec("GET /api/books", False, f"HTTP {st}，books 非空列表={bool(books)}")
        return ""
    rec("GET /api/books", True, f"200，{len(books)} 本")
    return str(books[0].get("id") or "")


def _check_tts_probe(port: int, checks: list[dict]) -> None:
    def rec(name: str, ok: bool, msg: str = "", kind: str = FAIL) -> None:
        checks.append({"name": name, "ok": ok, "msg": msg, "kind": kind})

    # §4.3 要的裸探针：/api/tts?probe=1 直接 200 + ok:true（不合成、不要求 text）
    try:
        st, h, raw = http_req(port, "/api/tts?probe=1")
    except Exception as exc:  # noqa: BLE001
        rec("GET /api/tts?probe=1", False, f"请求失败 {type(exc).__name__}: {exc}")
        st = None
    if st is not None:
        if st in (400, 404):
            rec("GET /api/tts?probe=1", False,
                f"HTTP {st}：probe=1 未生效（需在 text 非空校验之前处理 probe；契约 §1.1/§4.3 要求裸 probe 直接 200）",
                PENDING)
        elif st != 200:
            rec("GET /api/tts?probe=1", False,
                f"HTTP {st}（期望 200）；body={_short(raw.decode('utf-8', 'replace'), 120)}")
        else:
            data = _json_or_none(raw)
            missing = [k for k in ("ok", "engine", "cached", "chars")
                       if not isinstance(data, dict) or k not in data]
            if missing:
                rec("GET /api/tts?probe=1", False, f"200 但缺字段 {missing}（契约 §1.1）")
            elif data.get("ok") is not True:
                rec("GET /api/tts?probe=1", False, f"200 但 ok={data.get('ok')!r}（期望 true）")
            elif data.get("engine") not in ("edge", "sapi", "none"):
                rec("GET /api/tts?probe=1", False, f"engine={data.get('engine')!r} 不在 edge|sapi|none")
            else:
                rec("GET /api/tts?probe=1", True,
                    f"200 ok:true engine={data.get('engine')} cached={data.get('cached')} chars={data.get('chars')}")

    # 带 text 的探针：老调用 + 字段完整性（key/chars 需要 text 才有意义）
    text = quote("你好", safe="")
    try:
        st2, _, raw2 = http_req(port, f"/api/tts?probe=1&text={text}")
    except Exception as exc:  # noqa: BLE001
        rec("GET /api/tts?probe=1&text=…", False, f"请求失败 {type(exc).__name__}: {exc}")
        return
    data2 = _json_or_none(raw2) if st2 == 200 else None
    if st2 == 404:
        rec("GET /api/tts?probe=1&text=…", False, "HTTP 404：路由未实现（契约 §1.1）", PENDING)
    elif st2 != 200:
        rec("GET /api/tts?probe=1&text=…", False,
            f"HTTP {st2}（期望 200）；body={_short(raw2.decode('utf-8', 'replace'), 120)}")
    else:
        missing2 = [k for k in ("ok", "engine", "cached", "chars", "key")
                    if not isinstance(data2, dict) or k not in data2]
        if missing2:
            rec("GET /api/tts?probe=1&text=…", False, f"200 但缺字段 {missing2}（契约 §1.1）")
        elif data2.get("ok") is not True:
            rec("GET /api/tts?probe=1&text=…", False, f"200 但 ok={data2.get('ok')!r}（期望 true）")
        elif not isinstance(data2.get("key"), str) or len(data2.get("key")) != 20:
            rec("GET /api/tts?probe=1&text=…", False,
                f"key={data2.get('key')!r}（期望 sha1 前 20 位字符串）")
        else:
            rec("GET /api/tts?probe=1&text=…", True,
                f"200 ok:true chars={data2.get('chars')} key={data2.get('key')}")


def _check_book_files(port: int, book_id: str, checks: list[dict]) -> None:
    def rec(name: str, ok: bool, msg: str = "", kind: str = FAIL) -> None:
        checks.append({"name": name, "ok": ok, "msg": msg, "kind": kind})

    if not book_id:
        rec("GET /api/books/<id>/files", False, "拿不到第一本书 id，无法探测")
        return
    path = f"/api/books/{quote(book_id, safe='')}/files"
    try:
        st, _, raw = http_req(port, path)
    except Exception as exc:  # noqa: BLE001
        rec("GET /api/books/<id>/files", False, f"请求失败 {type(exc).__name__}: {exc}")
        return
    if st == 404:
        rec("GET /api/books/<id>/files", False,
            f"HTTP 404：路由未实现（契约 §1.3，owner voice-agent）book={book_id}", PENDING)
        return
    data = _json_or_none(raw)
    if st != 200:
        rec("GET /api/books/<id>/files", False, f"HTTP {st}（期望 200）")
        return
    files = (data or {}).get("files") if isinstance(data, dict) else None
    if not isinstance(files, list) or not files:
        rec("GET /api/books/<id>/files", False, f"200 但 files 非空列表={bool(files)}")
        return
    bad = [f.get("path") for f in files if not str(f.get("path", "")).startswith("reader/")]
    if bad:
        rec("GET /api/books/<id>/files", False, f"返回了非 reader/** 路径：{bad[:3]}")
        return
    nbytes = (data or {}).get("bytes")
    if not isinstance(nbytes, int) or nbytes <= 0:
        rec("GET /api/books/<id>/files", False, f"bytes={nbytes!r}（期望正整数）")
        return
    rec("GET /api/books/<id>/files", True, f"200，{len(files)} 个文件 / {nbytes} bytes")


def _check_sw(port: int, checks: list[dict]) -> None:
    def rec(name: str, ok: bool, msg: str = "", kind: str = FAIL) -> None:
        checks.append({"name": name, "ok": ok, "msg": msg, "kind": kind})

    try:
        st, h, raw = http_req(port, "/static/sw.js")
    except Exception as exc:  # noqa: BLE001
        rec("GET /static/sw.js", False, f"请求失败 {type(exc).__name__}: {exc}")
        return
    if st == 404:
        rec("GET /static/sw.js", False,
            "HTTP 404：sw.js 或 /static/ 目录路由未实现（契约 §1.4 + §3，owner voice-agent/web-agent）", PENDING)
        return
    if st != 200:
        rec("GET /static/sw.js", False, f"HTTP {st}（期望 200）")
        return
    allowed = h.get("service-worker-allowed")
    if allowed != "/":
        rec("GET /static/sw.js", False,
            f"Service-Worker-Allowed={allowed!r}（期望 '/'，契约 §1.4）")
        return
    ctype = h.get("content-type", "")
    if "javascript" not in ctype:
        rec("GET /static/sw.js", False, f"Content-Type={ctype!r}（期望 javascript）")
        return
    rec("GET /static/sw.js", True, "200 + Service-Worker-Allowed: /")


def _check_traversal(port: int, checks: list[dict]) -> None:
    def rec(name: str, ok: bool, msg: str = "", kind: str = FAIL) -> None:
        checks.append({"name": name, "ok": ok, "msg": msg, "kind": kind})

    probes = [
        ("/books/../../etc/passwd", "GET /books/../../etc/passwd"),
        ("/books/%2e%2e/%2e%2e/Windows/win.ini", "GET /books/%2e%2e/%2e%2e/Windows/win.ini"),
    ]
    for path, label in probes:
        try:
            st, _, raw = http_req(port, path)
        except Exception as exc:  # noqa: BLE001
            rec(label, False, f"请求失败 {type(exc).__name__}: {exc}")
            continue
        if st in (403, 404):
            rec(label, True, f"{st}（路径守卫生效）")
        else:
            rec(label, False,
                f"{st}：路径穿越没被挡住（契约 §1.5，必须 403/404）；body={_short(raw.decode('utf-8', 'replace'), 80)}")


def _service_once() -> tuple[list[dict], bool, str]:
    checks: list[dict] = []
    with ReaderServer() as srv:
        port = srv.port
        _check_headers(port, checks)
        book_id = _check_books(port, checks)
        _check_tts_probe(port, checks)
        _check_book_files(port, book_id, checks)
        _check_sw(port, checks)
        _check_traversal(port, checks)
        still_open = srv.port_still_open
        log_tail = srv.log_tail
    if still_open:
        checks.append({"name": "子进程清理", "ok": False,
                       "msg": f"探完端口 {port} 仍被监听，reader 子进程没清干净", "kind": FAIL})
    else:
        checks.append({"name": "子进程清理", "ok": True,
                       "msg": f"端口 {port} 已释放", "kind": OK})
    return checks, still_open, log_tail


def check_service(retries: int = RETRIES, delay: float = RETRY_DELAY) -> Result:
    res = Result("服务门")
    last_checks: list[dict] = []
    for attempt in range(1, retries + 1):
        try:
            last_checks, _, log_tail = _service_once()
        except RuntimeError as exc:
            res.fail(f"reader 启动失败：{exc}")
            return res
        pending = [c for c in last_checks if not c["ok"] and c["kind"] == PENDING]
        hard = [c for c in last_checks if not c["ok"] and c["kind"] != PENDING]
        if not pending and not hard:
            break
        if attempt < retries and pending and not hard:
            res.add(f"第 {attempt}/{retries} 次探测：{len(pending)} 项依赖未就绪，{delay:.0f}s 后重试")
            time.sleep(delay)
            continue
        break
    for c in last_checks:
        if c["ok"]:
            res.add(f"ok  {c['name']}  {c['msg']}")
        elif c["kind"] == PENDING:
            res.pending(f"{c['name']}  {c['msg']}")
        else:
            res.fail(f"{c['name']}  {c['msg']}")
    return res


# --------------------------------------------------------------------------- 4 覆盖门
def coverage_report(books_dir: Path | None = None) -> dict:
    """每本书：源页（去掉 curation.skip）vs reader 屏的 source_page 集合。

    返回 {"books": [...], "missing_reader": [id], "totals": {...}}，纯数据，供 eval 复用。
    """
    books_dir = Path(books_dir or (REPO / "books"))
    out: dict = {"books": [], "missing_reader": [], "totals": {}}
    if not books_dir.is_dir():
        out["totals"] = {"books": 0, "expected": 0, "covered": 0, "screens": 0}
        return out
    book_dirs = sorted(p for p in books_dir.iterdir() if p.is_dir())
    for bd in book_dirs:
        meta_p = bd / "book.json"
        info = {"id": bd.name, "ok": False, "reason": "", "src": 0, "skipped": [],
                "expected": 0, "screens": 0, "covered": 0, "missing": [], "extra": [],
                "has_reader": False}
        if not meta_p.is_file():
            info["reason"] = "缺 book.json"
            out["books"].append(info)
            continue
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            src_pages = meta.get("pages") or []
        except Exception as exc:  # noqa: BLE001
            info["reason"] = f"book.json 解析失败：{exc}"
            out["books"].append(info)
            continue
        n = len(src_pages)
        info["src"] = n
        cur_p = bd / "curation.json"
        cpages = {}
        if cur_p.is_file():
            try:
                cpages = (json.loads(cur_p.read_text(encoding="utf-8")) or {}).get("pages") or {}
            except Exception:  # noqa: BLE001
                cpages = {}
        skipped = sorted(pid for pid, c in cpages.items()
                         if str(pid).startswith("p") and isinstance(c, dict) and c.get("skip"))
        info["skipped"] = skipped
        expected_ids = [f"p{i:02d}" for i in range(1, n + 1) if f"p{i:02d}" not in set(skipped)]
        info["expected"] = len(expected_ids)
        reader_p = bd / "reader" / "book.json"
        if not reader_p.is_file():
            out["missing_reader"].append(bd.name)
            info["reason"] = "无 reader/book.json（未 analyze）"
            out["books"].append(info)
            continue
        try:
            rp = (json.loads(reader_p.read_text(encoding="utf-8")) or {}).get("pages") or []
        except Exception as exc:  # noqa: BLE001
            info["reason"] = f"reader/book.json 解析失败：{exc}"
            out["books"].append(info)
            continue
        info["screens"] = len(rp)
        info["has_reader"] = True
        # source_page -> 屏列表（用于分屏标记一致性）
        by_src: dict[int, list[dict]] = {}
        for pg in rp:
            sp = pg.get("source_page")
            try:
                sp = int(sp)
            except (TypeError, ValueError):
                continue
            by_src.setdefault(sp, []).append(pg)
        covered = sorted(by_src)
        covered_set = set(covered)
        expected_int = [i for i in range(1, n + 1) if f"p{i:02d}" in set(expected_ids)]
        missing = [i for i in expected_int if i not in covered_set]
        extra = [i for i in covered if i not in expected_int]
        info["covered"] = len([i for i in covered if i in expected_int])
        info["missing"] = [f"p{i:02d}" for i in missing]
        info["extra"] = [f"p{i:02d}" for i in extra]
        problems = []
        if missing:
            problems.append(f"缺 {len(missing)} 页：{', '.join(info['missing'])}")
        if extra:
            problems.append(f"多出 {len(extra)} 个源页（不在 book.json 或已 curation.skip）：{', '.join(info['extra'])}")
        bad_parts = []
        for sp, pages in sorted(by_src.items()):
            parts = [pg.get("parts") for pg in pages]
            part_idx = sorted(pg.get("part") for pg in pages)
            expect_parts = max([p for p in parts if isinstance(p, int)] or [len(pages)])
            expect_idx = list(range(1, expect_parts + 1))
            if part_idx != expect_idx:
                bad_parts.append(f"p{sp:02d} 分屏 part={part_idx} 期望 {expect_idx}")
        if bad_parts:
            problems.append("分屏标记不一致：" + "；".join(bad_parts[:3]))
        if problems:
            info["reason"] = "；".join(problems)
        else:
            info["ok"] = True
        out["books"].append(info)
    total_expected = sum(b["expected"] for b in out["books"] if b.get("has_reader"))
    total_covered = sum(b["covered"] for b in out["books"] if b.get("has_reader"))
    total_screens = sum(b["screens"] for b in out["books"] if b.get("has_reader"))
    out["totals"] = {"books": len(out["books"]), "expected": total_expected,
                     "covered": total_covered, "screens": total_screens,
                     "missing_reader": len(out["missing_reader"])}
    return out


def check_coverage(books_dir: Path | None = None) -> Result:
    res = Result("覆盖门")
    rep = coverage_report(books_dir)
    if not rep["books"] and not rep["missing_reader"]:
        res.fail("books/ 下没有任何书目录")
        return res
    for b in rep["books"]:
        if b["ok"]:
            res.add(f"ok  {b['id']}: 源页 {b['src']}"
                    f"{'（skip ' + str(len(b['skipped'])) + '）' if b['skipped'] else ''}"
                    f" → 期望 {b['expected']} 页，reader {b['screens']} 屏，覆盖 {b['covered']}/{b['expected']}")
        elif b["id"] in rep["missing_reader"]:
            continue  # 没有 reader 包只告警（未 analyze），不在非严格模式判红
        else:
            res.fail(f"{b['id']}: {b['reason']}")
    for bid in rep["missing_reader"]:
        res.warn(f"{bid}: 无 reader/book.json（未 analyze），覆盖未比对")
    t = rep["totals"]
    if t["expected"]:
        res.add(f"合计：期望 {t['expected']} 个源页，覆盖 {t['covered']}/{t['expected']}，"
                f"reader 共 {t['screens']} 屏（{t['books']} 本书，{t['missing_reader']} 本无 reader 包）")
    return res


# --------------------------------------------------------------------------- 5 前端门
def _front_once(web_dir: Path | None = None) -> Result:
    res = Result("前端门")
    web = Path(web_dir or (REPO / "book2real" / "web"))
    if not web.is_dir():
        res.fail(f"目录不存在：{web}")
        return res
    missing = [m for m in FRONT_MODULES if not (web / m).is_file()]
    for m in missing:
        res.fail(f"缺 web/{m}")
    node = shutil.which("node")
    js_present = [m for m in JS_MODULES if (web / m).is_file()]
    if js_present and not node:
        res.fail("node 不在 PATH，无法做 ESM 语法检查（契约 §4.1 要求 node --input-type=module --check）")
    elif node:
        for m in js_present:
            try:
                code, out, err = _run([node, "--input-type=module", "--check"],
                                      stdin_path=str(web / m), timeout=120)
            except subprocess.TimeoutExpired:
                res.fail(f"web/{m}: node --check 超时")
                continue
            if code == 0:
                res.add(f"ok  web/{m}: node --input-type=module --check")
            else:
                first = [ln for ln in (err or out).splitlines() if ln.strip()][:2]
                res.fail(f"web/{m}: node --check 失败 {_short(' | '.join(first))}")
    man = web / "manifest.json"
    if man.is_file():
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            res.fail(f"web/manifest.json 不是合法 JSON：{exc}")
        else:
            if not isinstance(data, dict):
                res.fail("web/manifest.json 顶层不是对象")
            else:
                res.add("ok  web/manifest.json: 合法 JSON")
                for k in ("name", "start_url", "scope", "display"):
                    if k not in data:
                        res.warn(f"manifest.json 缺契约字段 {k}（§3）")
                if data.get("display") != "standalone":
                    res.warn(f"manifest.json display={data.get('display')!r}（契约要求 standalone）")
    if not missing:
        res.add("ok  web/ 下 6 个模块齐全：" + " ".join(FRONT_MODULES))
    return res


def check_frontend(web_dir: Path | None = None, retries: int = RETRIES,
                   delay: float = RETRY_DELAY) -> Result:
    notes: list[str] = []
    res = _front_once(web_dir)
    for attempt in range(2, retries + 1):
        if res.ok:
            break
        notes.append(f"第 {attempt - 1}/{retries} 次未通过，{delay:.0f}s 后重试（容忍并行写入）")
        time.sleep(delay)
        res = _front_once(web_dir)
    if notes:
        res.lines = notes + res.lines
    return res


# --------------------------------------------------------------------------- 串门
def _retry_until_ok(fn, retries: int, delay: float) -> Result:
    notes: list[str] = []
    res = fn()
    for attempt in range(2, retries + 1):
        if res.ok:
            break
        notes.append(f"第 {attempt - 1}/{retries} 次未通过，{delay:.0f}s 后重试（容忍并行写入）")
        time.sleep(delay)
        res = fn()
    if notes:
        res.lines = notes + res.lines
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Book2Real 集成门（五门）")
    ap.add_argument("--strict", action="store_true", help="警告也算失败")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

    print(f"集成门 · Book2Real  repo={REPO}")
    print(f"python={PY}  strict={args.strict}")
    checks = [
        ("语法门", check_syntax),
        ("测试门", check_tests),
        ("服务门", check_service),
        ("覆盖门", check_coverage),
        ("前端门", check_frontend),
    ]
    passed = failed = 0
    for i, (name, fn) in enumerate(checks, start=1):
        t0 = time.time()
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001 - 门自身炸了也要给结论
            res = Result(name)
            res.fail(f"门自身异常 {type(exc).__name__}: {exc}")
        res.seconds = time.time() - t0
        strict_fail = args.strict and bool(res.warnings)
        ok = res.ok and not strict_fail
        status = "通过" if ok else "失败"
        if res.warnings:
            status += f"（{len(res.warnings)} 警告{'，严格模式计失败' if args.strict else ''}）"
        print(f"[{i}/5] {name}  {status}  ({res.seconds:.1f}s)")
        for line in res.lines:
            print(f"      {line}")
        for w in res.warnings:
            print(f"      警告  {w}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"结果：{passed} 通过，{failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
