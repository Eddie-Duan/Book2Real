#!/usr/bin/env python
"""门的自测：`python scripts/selftest_gate.py`

不碰真 books/ 与 web/（全部用临时目录），证明：
  覆盖门会对"少一页"的坏书判红并点名，好书包判绿；
  前端门会对"缺模块 / JS 语法坏 / manifest 坏 JSON"判红并点名，齐全时判绿；
  gate.py 的末行格式与退出码符合契约（`结果：N 通过，M 失败`，--strict 时警告计失败）；
  服务门的 ReaderServer 在用完后真的杀掉子进程、释放端口。
只用标准库。
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def expect(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond, detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   ← {detail}" if detail and not cond else ""))


def make_book(root: Path, bid: str, n_src: int, sources: list[dict] | None,
              skips: tuple[int, ...] = (), raw_rows: list[dict] | None = None) -> Path:
    """造一本假书：book.json 有 n_src 个源页；reader/book.json 覆盖 sources。

    sources: [{"src": 1, "parts": 1}, ...]；raw_rows 直接写 reader 页（测分屏标记不一致）。
    """
    bd = root / bid
    (bd / "pages").mkdir(parents=True, exist_ok=True)
    book = {"book_id": bid, "title": bid,
            "pages": [{"file": f"p{i:02d}.jpg"} for i in range(1, n_src + 1)]}
    (bd / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    if skips:
        cur = {"pages": {f"p{i:02d}": {"skip": True} for i in skips}}
        (bd / "curation.json").write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    if sources is None and raw_rows is None:
        return bd
    rdir = bd / "reader"
    rdir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    if raw_rows is not None:
        rows = raw_rows
    else:
        for pg in sources or []:
            parts = int(pg.get("parts", 1))
            for k in range(1, parts + 1):
                rows.append({"id": f"p{int(pg['src']):02d}" + ("" if parts == 1 else f"-{k}"),
                             "source_page": int(pg["src"]), "part": k, "parts": parts})
    (rdir / "book.json").write_text(json.dumps({"book_id": bid, "pages": rows}, ensure_ascii=False),
                                   encoding="utf-8")
    return bd


def make_web(root: Path) -> Path:
    web = root / "web"
    web.mkdir(parents=True, exist_ok=True)
    for js in gate.JS_MODULES:
        (web / js).write_text("export const ok = 1;\n", encoding="utf-8")
    (web / "manifest.json").write_text(json.dumps(
        {"name": "纸书智伴", "start_url": "/", "scope": "/", "display": "standalone"},
        ensure_ascii=False), encoding="utf-8")
    return web


def lines_of(res: gate.Result) -> str:
    return "\n".join(res.lines)


def coverage_case(root: Path, name: str) -> Path:
    """每个用例一个独立 books_dir，避免前一个用例的坏书污染后一个。"""
    d = root / name
    d.mkdir(parents=True)
    return d


def test_coverage() -> None:
    root = Path(tempfile.mkdtemp(prefix="b2r-selftest-books-"))
    try:
        # 1) 好书包：3 源页 → 3 屏
        case = coverage_case(root, "good")
        make_book(case, "good", 3, [{"src": 1}, {"src": 2}, {"src": 3}])
        res = gate.check_coverage(case)
        expect("覆盖门·好书包 判绿", res.ok, lines_of(res))
        expect("覆盖门·好书包 统计 3/3", "3/3" in lines_of(res), lines_of(res))

        # 2) 少一页的坏书：源页 3，reader 只有 p01/p02
        case = coverage_case(root, "bad_missing")
        make_book(case, "bad_missing", 3, [{"src": 1}, {"src": 2}])
        res = gate.check_coverage(case)
        txt = lines_of(res)
        expect("覆盖门·少一页的坏书 判红", not res.ok, txt)
        expect("覆盖门·少一页的坏书 点名 p03", "p03" in txt and "缺" in txt, txt)

        # 3) 多出一页（reader 引用了 book.json 里没有的源页）
        case = coverage_case(root, "bad_extra")
        make_book(case, "bad_extra", 3, [{"src": 1}, {"src": 2}, {"src": 3}, {"src": 4}])
        res = gate.check_coverage(case)
        txt = lines_of(res)
        expect("覆盖门·多页的坏书 判红", not res.ok, txt)
        expect("覆盖门·多页的坏书 点名 p04 且说多出", "p04" in txt and "多出" in txt, txt)

        # 4) curation.skip 生效：源页 3 但 p02 跳过，reader 覆盖 p01/p03
        case = coverage_case(root, "skip_ok")
        make_book(case, "skip_ok", 3, [{"src": 1}, {"src": 3}], skips=(2,))
        res = gate.check_coverage(case)
        expect("覆盖门·curation.skip 生效 判绿", res.ok, lines_of(res))

        # 5) skip 了却还留着屏（旧产物没重建）
        case = coverage_case(root, "stale_skip")
        make_book(case, "stale_skip", 3, [{"src": 1}, {"src": 2}, {"src": 3}], skips=(2,))
        res = gate.check_coverage(case)
        txt = lines_of(res)
        expect("覆盖门·skip 后残留旧屏 判红", not res.ok, txt)
        expect("覆盖门·残留旧屏 点名 p02", "p02" in txt, txt)

        # 6) 拆页：p02 拆成 2 屏，仍算覆盖完整
        case = coverage_case(root, "split_ok")
        make_book(case, "split_ok", 2, [{"src": 1, "parts": 1}, {"src": 2, "parts": 2}])
        res = gate.check_coverage(case)
        txt = lines_of(res)
        expect("覆盖门·拆页 判绿", res.ok, txt)
        expect("覆盖门·拆页 统计 3 屏", "3 屏" in txt, txt)

        # 7) 拆页标记坏了（part 编号跳号），必须红
        case = coverage_case(root, "split_bad")
        make_book(case, "split_bad", 2, None, raw_rows=[
            {"id": "p01", "source_page": 1, "part": 1, "parts": 1},
            {"id": "p02-1", "source_page": 2, "part": 1, "parts": 2},
            {"id": "p02-3", "source_page": 2, "part": 3, "parts": 2},
        ])
        res = gate.check_coverage(case)
        txt = lines_of(res)
        expect("覆盖门·分屏标记坏 判红", not res.ok, txt)
        expect("覆盖门·分屏标记坏 点名 p02", "p02" in txt and "分屏" in txt, txt)

        # 8) 没有 reader 包：非严格模式是警告（不算门红），严格模式才算失败
        case = coverage_case(root, "no_reader")
        make_book(case, "no_reader", 2, None)
        res = gate.check_coverage(case)
        expect("覆盖门·无 reader 包 是警告不是硬红", res.ok and any("no_reader" in w for w in res.warnings),
               lines_of(res) + " | warnings=" + str(res.warnings))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_frontend() -> None:
    if shutil.which("node") is None:
        expect("前端门·node 可用", False, "node 不在 PATH，无法自测前端门")
        return
    root = Path(tempfile.mkdtemp(prefix="b2r-selftest-web-"))
    try:
        web = make_web(root)
        res = gate.check_frontend(web, retries=1)
        expect("前端门·齐全的 web 判绿", res.ok, lines_of(res))

        # 缺模块
        (web / "qa.js").unlink()
        res = gate.check_frontend(web, retries=1)
        txt = lines_of(res)
        expect("前端门·缺 qa.js 判红", not res.ok, txt)
        expect("前端门·缺 qa.js 点名", "qa.js" in txt, txt)
        make_web(root)  # 复原

        # JS 语法坏
        (web / "tts.js").write_text("export const broken = ;\n", encoding="utf-8")
        res = gate.check_frontend(web, retries=1)
        txt = lines_of(res)
        expect("前端门·tts.js 语法坏 判红", not res.ok, txt)
        expect("前端门·tts.js 语法坏 点名 node --check", "tts.js" in txt and "node --check" in txt, txt)
        make_web(root)

        # manifest 坏 JSON
        (web / "manifest.json").write_text("{不是 json", encoding="utf-8")
        res = gate.check_frontend(web, retries=1)
        txt = lines_of(res)
        expect("前端门·manifest 坏 JSON 判红", not res.ok, txt)
        expect("前端门·manifest 坏 JSON 点名", "manifest.json" in txt, txt)
        make_web(root)

        # manifest 缺字段只是警告（--strict 才红）
        (web / "manifest.json").write_text(json.dumps({"name": "纸书智伴"}), encoding="utf-8")
        res = gate.check_frontend(web, retries=1)
        expect("前端门·manifest 缺字段 是警告不是硬红", res.ok and bool(res.warnings),
               lines_of(res) + " | warnings=" + str(res.warnings))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_gate_terminal_format() -> None:
    """monkeypatch 五门，只验输出末行格式与退出码。"""
    names = ["check_syntax", "check_tests", "check_service", "check_coverage", "check_frontend"]
    originals = {n: getattr(gate, n) for n in names}
    try:
        for n in names:
            setattr(gate, n, lambda n=n: gate.Result(n))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = gate.main([])
        last = buf.getvalue().strip().splitlines()[-1]
        expect("gate.main 全绿 末行固定格式", last == "结果：5 通过，0 失败", last)
        expect("gate.main 全绿 exit 0", code == 0, f"exit={code}")

        for n in names:
            setattr(gate, n, lambda n=n: gate.Result(n, warnings=["w"]))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = gate.main([])
        last = buf.getvalue().strip().splitlines()[-1]
        expect("gate.main 非严格 警告不变红", last == "结果：5 通过，0 失败" and code == 0, f"{last} exit={code}")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = gate.main(["--strict"])
        last = buf.getvalue().strip().splitlines()[-1]
        expect("gate.main --strict 警告变红", last == "结果：0 通过，5 失败" and code == 1, f"{last} exit={code}")

        setattr(gate, "check_syntax", originals["check_syntax"])
        setattr(gate, "check_tests", lambda: _fail_result())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = gate.main([])
        last = buf.getvalue().strip().splitlines()[-1]
        expect("gate.main 一门红 末行 4 通过 1 失败", last == "结果：4 通过，1 失败" and code == 1, f"{last} exit={code}")
    finally:
        for n, fn in originals.items():
            setattr(gate, n, fn)


def _fail_result() -> gate.Result:
    r = gate.Result("测试门")
    r.fail("自测故意失败")
    return r


def test_reader_cleanup() -> None:
    """真起一个 reader 子进程，验证用完端口释放（不留孤儿）。"""
    try:
        with gate.ReaderServer() as srv:
            port = srv.port
            during = gate.port_open(port)
        after = gate.port_open(port)
        expect("服务门·reader 启动后端口在监听", during, f"port={port}")
        expect("服务门·退出后端口已释放（子进程被杀）", not after, f"port={port} 仍在监听")
    except Exception as exc:  # noqa: BLE001
        expect("服务门·reader 启动/清理", False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    print(f"gate 自测 · repo={gate.REPO}")
    test_coverage()
    test_frontend()
    test_gate_terminal_format()
    test_reader_cleanup()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"selftest: {passed}/{len(RESULTS)} 通过，{failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
