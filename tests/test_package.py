"""package_submission 的自测（纯标准库，不依赖 pytest、不联网、不需要真的 git 仓库）。

    python tests/test_package.py

测试用临时目录伪造一个仓库（books/runs/docs/git 跟踪清单），
验证命名解析、dry-run 不落盘、.env 排除、缺失交互视频提示、
最终 zip 可被 zipfile 读回且含 SOURCE_MANIFEST.txt。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import traceback
import zipfile
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "package_submission.py"

_spec = importlib.util.spec_from_file_location("b2r_package_submission", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = mod          # dataclass 在 3.14 需要模块已注册
_spec.loader.exec_module(mod)

TRACKED: list[str] = []


# --------------------------------------------------------------------------- 夹具
def _write(root: Path, rel: str, data: bytes | str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(data)


def _fake_repo(tmp: str) -> tuple[Path, list[str]]:
    """构造假仓库文件；返回 (repo, 假的 git 跟踪清单)。"""
    root = Path(tmp) / "repo"
    files: dict[str, bytes | str] = {
        ".env": "SECRET_KEY=SHOULD_NOT_LEAK\n",
        ".env.local": "ANOTHER=SHOULD_NOT_LEAK\n",
        ".env.example": "IMAGE_API_KEY=\nVIDEO_API_KEY=\n",
        ".gitignore": ".env\nruns/\n",
        "README.md": "# 假仓库\n",
        "LICENSE": "MIT\n",
        "book2real/__init__.py": "",
        "book2real/app.py": "VALUE = 1\n",
        "scripts/package_submission.py": "# fake script\n",
        "books/charlie_lola/book.json": json.dumps(
            {"book_id": "charlie_lola", "title": "测试绘本", "pages": [
                {"file": "p01.jpg", "duration": 3.0},
                {"file": "p02.jpg", "duration": 3.0},
            ]}, ensure_ascii=False),
        "books/charlie_lola/curation.json": "{}\n",
        "books/charlie_lola/ORDER.csv": "p01.jpg\n",
        "books/charlie_lola/pages/p01.jpg": b"PAGE-IMAGE",
        "books/charlie_lola/reader/layers/p01_fg.png": b"READER-ARTIFACT",
        "runs/charlie_lola/reader_film/20260101-000000/film.mp4": b"OLD-FILM" * 200,
        "runs/charlie_lola/reader_film/20260102-000000/film.mp4": b"NEWEST-FILM" * 200,
        "docs/项目说明书.pdf": b"%PDF-1.4 fake statement\n",
        "docs/COMPLIANCE.md": "# 合规说明\n素材均已授权。\n",
        "assets/book1.jpg": b"OFFICIAL-PAGE",
        "assets/book2/scan.jpg": b"OFFICIAL-PAGE-2",
    }
    for rel, data in files.items():
        _write(root, rel, data)
    tracked = sorted(files)
    return root, tracked


@contextmanager
def _fake_git(tracked: list[str]):
    """把 git ls-files 换成固定清单，让测试不依赖 git 仓库状态。"""
    old = mod.list_tracked_files
    mod.list_tracked_files = lambda repo: list(tracked)
    try:
        yield
    finally:
        mod.list_tracked_files = old


def _run_main(argv: list[str], repo: Path) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = mod.main(argv, repo=repo)
    return code, buf.getvalue()


def _last_line(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# --------------------------------------------------------------------------- 测试
def test_parse_name_accepts_both_separators():
    expect = ("国科大", "计算机", "段柏旸")
    assert mod.parse_name("国科大-计算机-段柏旸") == expect
    assert mod.parse_name("国科大_计算机_段柏旸") == expect
    assert mod.parse_name(" 国科大 _ 计算机 _ 段柏旸 ") == expect
    assert mod.package_name(expect) == "国科大-计算机-段柏旸"


def test_parse_name_rejects_wrong_segment_count_and_path_chars():
    for bad in ("国科大-段柏旸", "国科大-计算机-段柏旸-多一段", "", "国科大-../x-段柏旸"):
        try:
            mod.parse_name(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"应当拒绝：{bad!r}")
    with tempfile.TemporaryDirectory() as td:
        repo, _tracked = _fake_repo(td)
        code, text = _run_main(["--name", "只有两段", "--out", str(Path(td) / "dist")], repo)
        assert code == 2, code
        assert "用法" in text and "--name" in text


def test_dry_run_writes_nothing_and_prints_plan():
    with tempfile.TemporaryDirectory() as td:
        repo, tracked = _fake_repo(td)
        out = Path(td) / "dist"
        with _fake_git(tracked):
            code, text = _run_main(
                ["--name", "甲学校-乙院系-丙同学", "--out", str(out), "--dry-run"], repo)
        assert code == 0, (code, text)
        assert not out.exists(), "dry-run 不应创建输出目录"
        assert "演练" in text
        assert "项目源代码.zip" in text
        assert "结果：" in _last_line(text) and _last_line(text).endswith("失败")


def test_source_zip_excludes_env_and_big_binaries_but_keeps_manifest():
    with tempfile.TemporaryDirectory() as td:
        repo, tracked = _fake_repo(td)
        plan = mod.build_source_plan(repo, tracked=tracked, package="甲-乙-丙")
        dest = Path(td) / "项目源代码.zip"
        mod.write_source_zip(plan, dest, repo)

        with zipfile.ZipFile(dest) as zf:
            names = set(zf.namelist())
            assert "SOURCE_MANIFEST.txt" in names
            assert ".env.example" in names
            assert ".env" not in names and ".env.local" not in names
            assert "README.md" in names
            assert "book2real/app.py" in names
            assert "books/charlie_lola/book.json" in names
            assert "books/charlie_lola/curation.json" in names
            assert not any(n.startswith("runs/") for n in names)
            assert not any(n.startswith("books/charlie_lola/reader/") for n in names)
            assert not any(n.startswith("books/charlie_lola/pages/") for n in names)
            assert "assets/book1.jpg" not in names
            assert not any(n.startswith("assets/book2/") for n in names)

            manifest = zf.read("SOURCE_MANIFEST.txt").decode("utf-8")
            assert "排除项及原因" in manifest
            assert ".env" in manifest and "books/*/reader/" in manifest

            for name in names:
                assert b"SHOULD_NOT_LEAK" not in zf.read(name), f"密钥泄漏进 {name}"


def test_missing_interactive_video_is_reported_not_faked():
    with tempfile.TemporaryDirectory() as td:
        repo, tracked = _fake_repo(td)
        out = Path(td) / "dist"
        with _fake_git(tracked):
            code, text = _run_main(["--name", "甲学校-乙院系-丙同学", "--out", str(out)], repo)
        assert code == 0, (code, text)
        assert "缺失：需要录制交互演示" in text

        target = out / "甲学校-乙院系-丙同学"
        readme = (target / mod.README_NAME).read_text(encoding="utf-8")
        assert "缺失：需要录制交互演示" in readme
        # 不伪造：目录里不应出现交互演示视频文件
        assert not any("交互演示视频" in p.name for p in target.iterdir())
        # 非交互视频仍然按最新 film 复制
        assert (target / "测试绘本+非交互视频.mp4").read_bytes() == b"NEWEST-FILM" * 200


def test_final_zip_is_readable_and_contains_source_manifest():
    with tempfile.TemporaryDirectory() as td:
        repo, tracked = _fake_repo(td)
        # 补一条交互录屏（文件名含“交互”），让四种官方材料齐全
        interactive = "runs/charlie_lola/interactive/交互演示录屏.mp4"
        _write(repo, interactive, b"INTERACTIVE-RECORDING" * 100)
        tracked = sorted(tracked + [interactive])
        out = Path(td) / "dist"
        with _fake_git(tracked):
            code, text = _run_main(["--name", "甲学校-乙院系-丙同学", "--out", str(out)], repo)
        assert code == 0, (code, text)
        assert "缺失：需要录制交互演示" not in text

        package = "甲学校-乙院系-丙同学"
        final = out / f"{package}.zip"
        assert final.is_file() and final.stat().st_size > 0

        with zipfile.ZipFile(final) as zf:
            names = set(zf.namelist())
            for rel in (
                "项目说明书.pdf",
                "项目源代码.zip",
                "测试绘本+非交互视频.mp4",
                "测试绘本+交互演示视频.mp4",
                "素材来源清单.md",
                "知识产权承诺书.md",
                mod.README_NAME,
            ):
                assert f"{package}/{rel}" in names, f"最终包缺 {rel}"

            assert zf.read(f"{package}/测试绘本+非交互视频.mp4") == b"NEWEST-FILM" * 200
            assert zf.read(f"{package}/测试绘本+交互演示视频.mp4") == b"INTERACTIVE-RECORDING" * 100

            inner = zf.read(f"{package}/项目源代码.zip")
            with zipfile.ZipFile(io.BytesIO(inner)) as izf:
                inner_names = set(izf.namelist())
                assert "SOURCE_MANIFEST.txt" in inner_names
                assert ".env.example" in inner_names
                assert ".env" not in inner_names
                assert "books/charlie_lola/book.json" in inner_names

            material = zf.read(f"{package}/素材来源清单.md").decode("utf-8")
            assert "docs/COMPLIANCE.md" in material
            assert "测试绘本" in material
            ip = zf.read(f"{package}/知识产权承诺书.md").decode("utf-8")
            assert "甲学校" in ip and "丙同学" in ip


def test_source_plan_counts_and_reasons():
    with tempfile.TemporaryDirectory() as td:
        repo, tracked = _fake_repo(td)
        plan = mod.build_source_plan(repo, tracked=tracked, package="甲-乙-丙")
        included = {rel for rel, _ in plan.included}
        excluded = {rel: reason for rel, reason in plan.excluded}
        assert ".env" in excluded and ".env.local" in excluded
        assert "runs/charlie_lola/reader_film/20260102-000000/film.mp4" in excluded
        assert "books/charlie_lola/reader/layers/p01_fg.png" in excluded
        assert "books/charlie_lola/pages/p01.jpg" in excluded
        assert "assets/book2/scan.jpg" in excluded
        assert "books/charlie_lola/book.json" in included
        assert plan.total_bytes > 0
        assert plan.manifest.startswith("Book2Real 项目源代码包")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:  # noqa: BLE001 - 自跑入口：打印 traceback 后非零退出
                traceback.print_exc()
                sys.exit(1)
            passed += 1
            print(f"ok  {name}")
    print(f"{passed} tests passed")
