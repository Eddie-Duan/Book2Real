#!/usr/bin/env python3
"""运行环境自检与自动补装：缺什么装什么，其余一概不动。

只做三件事：
  1. 检查 ffmpeg / ffprobe 是否可用；缺失则用系统包管理器安装（Windows 走 winget）。
  2. 检查 Python 版本与依赖包；缺失则装进一个隔离的 .venv（不污染系统解释器）。
  3. 复核结果并打印环境清单；仍有缺口时打印手工修复命令并以退出码 1 结束。

本脚本只用标准库，因此可以在"一个第三方包都没有"的裸解释器下直接运行。

用法:
  python scripts/bootstrap_env.py               # 检测 + 自动补装（默认）
  python scripts/bootstrap_env.py --check       # 只检测不安装，不改动系统
  python scripts/bootstrap_env.py --json        # 机器可读输出
  python scripts/bootstrap_env.py --no-venv     # 不建 .venv，直接装进当前解释器
  python scripts/bootstrap_env.py --with-torch  # 额外补装 torch（体积大，按需）
  python scripts/bootstrap_env.py --mirror https://pypi.tuna.tsinghua.edu.cn/simple

退出码: 0 = 全部就绪；1 = 仍有缺失项。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = PROJECT_ROOT / ".venv"

# (导入名, pip 分发名)
REQUIRED = [
    ("numpy", "numpy"),
    ("PIL", "Pillow"),
    ("cv2", "opencv-python-headless"),
    ("yaml", "PyYAML"),
    ("skimage", "scikit-image"),
    ("imagehash", "imagehash"),
    ("requests", "requests"),
]
OPTIONAL = [
    ("pytest", "pytest"),
    ("torch", "torch"),
]

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


def force_utf8_when_piped() -> None:
    """输出被管道/重定向捕获时（非终端），改用 UTF-8，避免 Windows 本地代码页把中文写坏。

    终端直连时不动，仍由 Python 走 WriteConsoleW，中文正常。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure") and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def run(cmd: list, timeout: int = 600) -> tuple:
    """执行外部命令，返回 (returncode, stdout+stderr)。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(map(str, cmd))}"


# ---------------------------------------------------------------- ffmpeg 部分


def _ffmpeg_candidate_dirs() -> list:
    """winget/常见安装器把可执行文件放这儿，装完但当前进程 PATH 还没刷新时靠它找回。"""
    dirs = []
    if IS_WIN:
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            dirs.append(Path(local) / "Microsoft" / "WinGet" / "Links")
            pkgroot = Path(local) / "Microsoft" / "WinGet" / "Packages"
            if pkgroot.is_dir():
                for pkg in pkgroot.glob("Gyan.FFmpeg*"):
                    dirs.extend(sorted(pkg.rglob("bin")))
        dirs.extend(
            [
                Path(r"C:\ffmpeg\bin"),
                Path(r"C:\Program Files\ffmpeg\bin"),
                Path(r"C:\ProgramData\chocolatey\bin"),
            ]
        )
    else:
        dirs.extend([Path("/usr/local/bin"), Path("/opt/homebrew/bin"), Path("/usr/bin")])
    return [d for d in dirs if d.is_dir()]


def refresh_path() -> None:
    """把候选目录前置到本进程 PATH，使同一进程内的后续调用也能命中新装的工具。"""
    extra = [str(d) for d in _ffmpeg_candidate_dirs()]
    if not extra:
        return
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    for d in extra:
        if d not in parts:
            parts.insert(0, d)
    os.environ["PATH"] = os.pathsep.join(parts)


def probe_ffmpeg() -> dict:
    already_on_path = shutil.which("ffmpeg") is not None
    refresh_path()
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    info = {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "version": None, "ok": False,
            "on_system_path": already_on_path}
    if ffmpeg:
        rc, out = run([ffmpeg, "-version"], timeout=60)
        if rc == 0:
            info["ok"] = bool(ffprobe)
            first = out.splitlines()[0] if out.splitlines() else ""
            info["version"] = first.strip() or None
        else:
            info["ffmpeg"] = None
    return info


def install_ffmpeg(timeout: int = 900) -> tuple:
    """按平台选择包管理器安装 ffmpeg，返回 (是否发起安装, 说明)。"""
    if IS_WIN:
        winget = shutil.which("winget")
        if not winget:
            return False, "未找到 winget，请手动安装 ffmpeg 并确保其在 PATH 中"
        cmd = [
            winget,
            "install",
            "--id",
            "Gyan.FFmpeg",
            "-e",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--disable-interactivity",
        ]
        rc, out = run(cmd, timeout=timeout)
        tail = "\n".join(out.strip().splitlines()[-6:])
        return rc == 0, tail

    if IS_MAC:
        if not shutil.which("brew"):
            return False, "未找到 brew，请先安装 Homebrew 或手动安装 ffmpeg"
        rc, out = run(["brew", "install", "ffmpeg"], timeout=timeout)
        return rc == 0, "\n".join(out.strip().splitlines()[-6:])

    if shutil.which("apt-get"):
        prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
        rc, out = run(prefix + ["apt-get", "install", "-y", "ffmpeg"], timeout=timeout)
        return rc == 0, "\n".join(out.strip().splitlines()[-6:])
    if shutil.which("dnf"):
        prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
        rc, out = run(prefix + ["dnf", "install", "-y", "ffmpeg"], timeout=timeout)
        return rc == 0, "\n".join(out.strip().splitlines()[-6:])
    if shutil.which("pacman"):
        prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
        rc, out = run(prefix + ["pacman", "-S", "--noconfirm", "ffmpeg"], timeout=timeout)
        return rc == 0, "\n".join(out.strip().splitlines()[-6:])
    return False, "未识别的包管理器，请手动安装 ffmpeg"


# ------------------------------------------------------------- Python 环境部分


def in_venv() -> bool:
    return Path(sys.prefix) != Path(sys.base_prefix)


def bundled_python() -> Path | None:
    """DSH 内置 3.12 运行时（自带 numpy/Pillow），有则优先作为 .venv 基座。"""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    for cand in (
        Path(local) / "Programs" / "DeepSeek Harness" / "resources" / "app.asar" / "dsh-runtimes",
        Path.home() / ".dsh" / "dsh-runtimes",
    ):
        if not cand.is_dir():
            continue
        for py in sorted(cand.glob("*/dependencies/python/python.exe")):
            rc, out = run([str(py), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"], timeout=60)
            if rc == 0 and out.strip().startswith("3.12"):
                return py
    return None


def venv_python(venv_dir: Path) -> Path | None:
    cand = venv_dir / ("Scripts/python.exe" if IS_WIN else "bin/python")
    return cand if cand.is_file() else None


def ensure_venv(base_py: Path, say) -> Path | None:
    """.venv 不存在则用 base_py 创建；带 --system-site-packages 以便继承基座已有的 numpy/Pillow。"""
    existing = venv_python(VENV_DIR)
    if existing:
        say(f".venv 已存在，直接复用: {existing}")
        return existing
    say(f"创建 .venv（基座 {base_py}）...")
    rc, out = run([str(base_py), "-m", "venv", "--system-site-packages", str(VENV_DIR)], timeout=600)
    if rc != 0:
        say(f"[WARN] .venv 创建失败: {out.strip()[:400]}")
        return None
    existing = venv_python(VENV_DIR)
    if not existing:
        say("[WARN] .venv 创建后未找到 python 可执行文件")
        return None
    run([str(existing), "-m", "pip", "install", "--upgrade", "--no-input", "pip"], timeout=600)
    return existing


def resolve_target(args, say, install: bool = True) -> tuple:
    """决定在哪个解释器里安装依赖，返回 (python 路径, 选择理由)。"""
    if args.python:
        return Path(args.python), "命令行显式指定 --python"
    if in_venv():
        return Path(sys.executable), "当前已在虚拟环境中"
    if args.no_venv:
        return Path(sys.executable), "使用 --no-venv，装进当前解释器"
    base = bundled_python()
    if base:
        if not install:
            reused = venv_python(VENV_DIR)
            if reused:
                return reused, "复用已有 .venv（仅检测，不创建）"
            return base, "仅检测：实际运行时会用内置 3.12 新建 .venv 并装依赖"
        py = ensure_venv(base, say)
        if py:
            return py, "新建/复用 .venv（基座为内置 3.12 运行时，已含 numpy 与 Pillow）"
    return Path(sys.executable), "未找到可作基座的内置运行时，装进当前解释器"


def probe_modules(py: Path, names: list) -> list:
    """在目标解释器里检查模块可否导入，返回缺失的导入名列表。"""
    code = (
        "import importlib.util, json\n"
        f"names = {names!r}\n"
        "print(json.dumps([n for n in names if importlib.util.find_spec(n) is None]))\n"
    )
    rc, out = run([str(py), "-c", code], timeout=180)
    if rc != 0:
        return list(names)
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("["):
            try:
                return json.loads(line)
            except Exception:
                continue
    return list(names)


def pip_install(py: Path, dists: list, mirror: str | None, say) -> tuple:
    if not dists:
        return True, "无需安装"
    cmd = [str(py), "-m", "pip", "install", "--no-input", "--disable-pip-version-check"]
    if mirror:
        cmd += ["--index-url", mirror]
    cmd += dists
    say("pip install " + " ".join(dists))
    rc, out = run(cmd, timeout=3600)
    if rc != 0:
        tail = "\n".join(out.strip().splitlines()[-12:])
        return False, tail
    return True, "pip install 成功"


def requirements_hint(py: Path) -> str:
    rc, out = run([str(py), "-c", "import sys;print(sys.version_info[0]*100+sys.version_info[1])"], timeout=60)
    ver = out.strip()
    if rc == 0 and ver.isdigit() and int(ver) >= 313:
        return "requirements-py313.txt"
    return "requirements.txt"


def parse_requirements(path: Path) -> dict:
    """把 requirements 文件解析成 {分发名(小写): 原始约束行}，忽略注释、空行与 -r/-e 指令。"""
    specs = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return specs
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", " ", ";"):
            idx = name.find(sep)
            if idx > 0:
                name = name[:idx]
        name = name.strip().lower()
        if name:
            specs[name] = line
    return specs


def pinned_specs(py: Path) -> dict:
    """按目标解释器版本选 requirements 文件，返回其钉版/区间约束（用于避免装到全新大版本）。"""
    return parse_requirements(PROJECT_ROOT / requirements_hint(py))


# ------------------------------------------------------------------------ main


def main() -> int:
    force_utf8_when_piped()
    parser = argparse.ArgumentParser(
        description="运行环境自检与自动补装：缺什么装什么，其余不动。"
    )
    parser.add_argument("--check", action="store_true", help="只检测，不安装任何东西")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果（隐含不改动系统）")
    parser.add_argument("--no-venv", action="store_true", help="不创建 .venv，依赖装进当前解释器")
    parser.add_argument("--with-torch", action="store_true", help="额外补装 torch（体积大）")
    parser.add_argument("--python", default=None, help="显式指定用于安装依赖的解释器路径")
    parser.add_argument("--mirror", default=None, help="pip 镜像源，如 https://pypi.tuna.tsinghua.edu.cn/simple")
    args = parser.parse_args()

    install = not (args.check or args.json)

    def say(msg: str) -> None:
        if not args.json:
            print(msg, flush=True)

    report = {
        "platform": f"{platform.system()} {platform.release()}",
        "ffmpeg": {},
        "python": {},
        "packages": {},
        "ready": False,
    }
    say(f"=== 环境自检 ({report['platform']}) ===")
    say("模式: " + ("仅检测" if not install else "检测 + 自动补装") + "\n")

    # --- 1. ffmpeg / ffprobe
    say("[1/3] ffmpeg / ffprobe")
    ff = probe_ffmpeg()
    if ff["ok"]:
        say(f"  [OK] 已就绪: {ff['version']}")
    elif install:
        say("  [MISS] 未检测到，开始安装 ...")
        ok, note = install_ffmpeg()
        if ok:
            ff = probe_ffmpeg()
        if ff["ok"]:
            say(f"  [OK] 安装完成: {ff['version']}")
            if not ff.get("on_system_path"):
                say("  注: ffmpeg 不在系统 PATH 中，本 skill 的 common.py 会自动定位其所在目录；")
                say(f"      其他工具若也需要 ffmpeg，请把 {str(Path(ff['ffmpeg']).parent)} 加入 PATH 或重开终端。")
        else:
            say("  [FAIL] 自动安装未成功，请手动执行: winget install Gyan.FFmpeg")
            if note:
                say("  " + note.replace("\n", "\n  "))
    else:
        say("  [MISS] 未检测到（--check 模式不安装）；安装命令: winget install Gyan.FFmpeg")
    report["ffmpeg"] = {
        "ok": bool(ff["ok"]),
        "ffmpeg": ff["ffmpeg"],
        "ffprobe": ff["ffprobe"],
        "version": ff["version"],
    }
    say("")

    # --- 2. Python 解释器
    say("[2/3] Python 解释器与依赖")
    target, reason = resolve_target(args, lambda m: say("  " + m), install=install)
    rc, out = run([str(target), "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"], timeout=60)
    pyver = out.strip() if rc == 0 else "unknown"
    say(f"  目标解释器: {target}")
    say(f"  版本 {pyver}（{reason}）")

    names = [m for m, _ in REQUIRED]
    if args.with_torch:
        names += [m for m, _ in OPTIONAL if m == "torch"]
    missing = probe_modules(target, names)
    say(f"  必需包: {'全齐' if not missing else '缺 ' + str(len(missing)) + ' 个'}")

    if missing and install:
        dist_by_module = {m: d for m, d in REQUIRED + OPTIONAL}
        dists = [dist_by_module[n] for n in missing if n in dist_by_module]
        # 优先按 requirements 文件里的钉版/区间安装：裸包名会拉到全新大版本（例如 OpenCV 5.x），
        # 与 scripts/ 里已按 4.x 调过的检测阈值、以及 tests/ 的合成样本行为不一致。
        pins = pinned_specs(target)
        pinned = [pins.get(d.lower(), d) for d in dists]
        say("  缺失: " + ", ".join(pinned))
        ok, note = pip_install(target, pinned, args.mirror, say)
        if not ok and any(p != d for p, d in zip(pinned, dists)):
            say("  [WARN] 钉版安装失败，回退到按包名安装最新版:")
            say("  " + note.replace("\n", "\n  "))
            ok, note = pip_install(target, dists, args.mirror, say)
        if not ok:
            say("  [WARN] 按包名安装失败，回退到 requirements 文件整体安装:")
            say("  " + note.replace("\n", "\n  "))
            req = PROJECT_ROOT / requirements_hint(target)
            if req.is_file():
                cmd_retry = [str(target), "-m", "pip", "install", "--no-input", "--disable-pip-version-check"]
                if args.mirror:
                    cmd_retry += ["--index-url", args.mirror]
                cmd_retry += ["-r", str(req)]
                rc, out = run(cmd_retry, timeout=3600)
                if rc != 0:
                    say(f"  [FAIL] 回退安装仍失败，请手动执行: {target} -m pip install -r {req.name}")
                    say("  " + "\n".join(out.strip().splitlines()[-10:]).replace("\n", "\n  "))
        missing = probe_modules(target, names)

    say(f"  复核结果: {'全部可导入' if not missing else '仍缺: ' + ', '.join(missing)}")
    report["python"] = {
        "executable": str(target),
        "version": pyver,
        "reason": reason,
        "requirements_file": requirements_hint(target),
    }
    report["packages"] = {
        "required": {m: (m not in missing) for m, _ in REQUIRED},
        "missing": missing,
    }
    say("")

    # --- 3. 结论
    say("[3/3] 结论")
    ready = bool(report["ffmpeg"]["ok"]) and not missing
    report["ready"] = ready
    if ready:
        say("  [OK] 环境就绪，可以运行 preflight.py --check-env 与后续流程。")
        if target != Path(sys.executable) and not in_venv():
            act = VENV_DIR / ("Scripts/Activate.ps1" if IS_WIN else "bin/activate")
            say(f"  后续命令请先激活环境: {act}")
    else:
        say("  [ACTION] 仍有缺口，处理后重跑本脚本：")
        if not report["ffmpeg"]["ok"]:
            say("    - ffmpeg: winget install Gyan.FFmpeg  然后重开终端")
        if missing:
            say(f"    - 依赖: {target} -m pip install -r {report['python']['requirements_file']}")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
