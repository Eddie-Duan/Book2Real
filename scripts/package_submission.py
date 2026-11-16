#!/usr/bin/env python
"""企业预选赛提交包打包器：`python scripts/package_submission.py --name "学校-院系-姓名" --out dist`

产出（依据 docs/答疑/答疑总结.md §3.1 与企业赛材料要求 docs/纸书智伴.pdf 第 3–4 页）：

    dist/<学校-院系-姓名>/
      项目说明书.pdf                      （仓库里没有就不伪造，标记缺失）
      项目源代码.zip                      （只含 git 跟踪文件 + SOURCE_MANIFEST.txt）
      <绘本名称>+非交互视频.mp4             （runs/<book>/reader_film/<时间戳>/film.mp4 最新一份）
      <绘本名称>+交互演示视频.mp4           （找不到就明确报“缺失：需要录制交互演示”）
      素材来源清单.md
      知识产权承诺书.md
      README-提交说明.md                  （清单、缺失项、体积）
    dist/<学校-院系-姓名>.zip              （上面整个目录再压一层，官方要求的文件名）

约定：
  * `--name` 用 `-` 或 `_` 分隔学校/院系/姓名三段，输出统一成 `学校-院系-姓名`。
  * 交互演示视频需要人录屏，脚本不会生成、不会拿非交互视频冒充；缺失项写进 README。
  * 源码包排除 .env*（.env.example 除外）、runs/、books/*/reader/、books/*/pages/、
    assets/book2/ 等大二进制与运行产物；排除规则和原因写在包内 SOURCE_MANIFEST.txt。
  * 体积上限：单包 2GB（保守值）；单文件 >500MB 警告。不联网、不写密码。
  * 末行固定 `结果：N 通过，M 失败`；硬性检查失败 exit 1，参数错误 exit 2。
  * `--dry-run` 只打印计划与体积，不创建任何目录/文件。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

MAX_ZIP_BYTES = 2 * 1024 ** 3          # 预选赛上传限制未知，先按保守值 2GB
WARN_FILE_BYTES = 500 * 1024 ** 2      # 单文件超过 500MB 给警告
MIN_VIDEO_BYTES = 1024                 # 小于 1KB 的 mp4 基本是失败产物

SOURCE_ZIP_NAME = "项目源代码.zip"
STATEMENT_NAME = "项目说明书.pdf"
MANIFEST_NAME = "SOURCE_MANIFEST.txt"
README_NAME = "README-提交说明.md"
MATERIAL_NAME = "素材来源清单.md"
IP_NAME = "知识产权承诺书.md"

NAME_SPLIT = re.compile(r"[-_]")
BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
BAD_FILE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


# --------------------------------------------------------------------------- 通用
class PackagingError(RuntimeError):
    """打包过程中的硬错误（参数/输入/写盘）。"""


@dataclass
class Report:
    checks: list[tuple[bool, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def ok(self, msg: str) -> None:
        self.checks.append((True, msg))

    def fail(self, msg: str) -> None:
        self.checks.append((False, msg))

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def passed(self) -> int:
        return sum(1 for ok, _ in self.checks if ok)

    @property
    def failed(self) -> int:
        return sum(1 for ok, _ in self.checks if not ok)


def parse_name(raw: str) -> tuple[str, str, str]:
    """把 `学校-院系-姓名` / `学校_院系_姓名` 解析成三段。不足或非法直接 ValueError。"""
    text = (raw or "").strip()
    parts = [p.strip() for p in NAME_SPLIT.split(text)]
    parts = [p for p in parts if p]
    if len(parts) != 3:
        raise ValueError(
            f"--name 需要三段（学校-院系-姓名），收到 {len(parts)} 段：{raw!r}")
    for part in parts:
        if part in (".", "..") or BAD_NAME_CHARS.search(part):
            raise ValueError(f"--name 的段含有路径/文件名非法字符：{part!r}")
    return parts[0], parts[1], parts[2]


def package_name(parts: tuple[str, str, str]) -> str:
    return "-".join(parts)


def fmt_size(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def safe_filename(text: str, fallback: str) -> str:
    """绘本名做文件名：去掉 Windows 非法字符与首尾空白/点。"""
    name = BAD_FILE_CHARS.sub("_", (text or "").strip()).strip(" .")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = fallback
    return name[:80]


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- 书与视频
@dataclass
class BookPlan:
    book_id: str
    title: str
    pages: int = 0
    film: Path | None = None
    film_dir: str = ""
    interactive: Path | None = None
    notes: list[str] = field(default_factory=list)


def load_books(books_dir: Path, report: Report) -> list[BookPlan]:
    if not books_dir.is_dir():
        report.fail(f"books 目录不存在：{books_dir}")
        return []
    books: list[BookPlan] = []
    for bd in sorted(books_dir.iterdir()):
        if not bd.is_dir():
            continue
        meta = bd / "book.json"
        if not meta.is_file():
            continue
        title, pages = bd.name, 0
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            title = str(data.get("title") or bd.name)
            pages = len(data.get("pages") or [])
        except Exception as exc:  # noqa: BLE001 - 书目录坏了只报告，不中断打包
            report.warn(f"{bd.name}/book.json 解析失败，书名用目录名：{exc}")
        books.append(BookPlan(book_id=bd.name, title=title, pages=pages))
    if not books:
        report.fail(f"{books_dir} 下没有任何带 book.json 的绘本")
    else:
        report.ok(f"绘本 {len(books)} 本：" + "、".join(b.title for b in books))
    return books


def latest_film(runs_dir: Path, book_id: str) -> tuple[Path | None, str, list[Path]]:
    """在 runs/<book>/reader_film/ 下按目录名（时间戳）取最新 film.mp4。

    返回 (最新可用 film, 时间戳目录名, 被跳过的疑似坏文件)。
    """
    root = runs_dir / book_id / "reader_film"
    candidates: list[tuple[str, Path]] = []
    if root.is_dir():
        for d in root.iterdir():
            if not d.is_dir():
                continue
            film = d / "film.mp4"
            if film.is_file():
                candidates.append((d.name, film))
        direct = root / "film.mp4"
        if direct.is_file():
            candidates.append(("(direct)", direct))
    candidates.sort(key=lambda item: item[0], reverse=True)
    bad: list[Path] = []
    for _stamp, film in candidates:
        try:
            size = film.stat().st_size
        except OSError:
            bad.append(film)
            continue
        if size >= MIN_VIDEO_BYTES:
            return film, _stamp, bad
        bad.append(film)
    return None, "", bad


def find_interactive(runs_dir: Path, book_id: str) -> Path | None:
    """找这本书的交互演示录屏：文件名/相对路径含 交互、interactive、录屏、demo 的 mp4。"""
    root = runs_dir / book_id
    if not root.is_dir():
        return None
    hits: list[tuple[float, str, Path]] = []
    for p in root.rglob("*.mp4"):
        if not p.is_file() or p.name.lower() == "film.mp4":
            continue
        rel = p.relative_to(root).as_posix()
        low = rel.lower()
        if not any(k in rel or k in low for k in ("交互", "interactive", "录屏", "demo")):
            continue
        try:
            if p.stat().st_size < MIN_VIDEO_BYTES:
                continue
            hits.append((p.stat().st_mtime, rel, p))
        except OSError:
            continue
    if not hits:
        return None
    hits.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return hits[0][2]


def plan_videos(books: list[BookPlan], runs_dir: Path, report: Report) -> None:
    if not runs_dir.is_dir():
        report.warn(f"runs 目录不存在：{runs_dir}（所有视频都会标记缺失）")
    used: dict[str, str] = {}
    for book in books:
        film, stamp, bad = latest_film(runs_dir, book.book_id)
        book.film, book.film_dir = film, stamp
        for old in bad:
            report.warn(f"跳过疑似损坏的视频（<1KB）：{old}")
        if film is None:
            report.warn(f"缺失：{book.title} 的非交互视频（runs/{book.book_id}/reader_film/*/film.mp4 不存在）")
        else:
            report.ok(f"非交互视频 {book.title} ← {film.relative_to(runs_dir).as_posix()}（{fmt_size(film.stat().st_size)}）")
        book.interactive = find_interactive(runs_dir, book.book_id)
        if book.interactive is None:
            report.warn(
                "缺失：需要录制交互演示（"
                f"{book.title} / {book.book_id}）——未在 runs/{book.book_id}/ 下找到交互录屏 mp4；"
                f"录制后放到 runs/{book.book_id}/interactive.mp4 或 "
                f"runs/{book.book_id}/reader_film/<时间戳>/interactive.mp4")
        else:
            report.ok(f"交互演示 {book.title} ← {book.interactive.relative_to(runs_dir).as_posix()}")
        key = safe_filename(book.title, book.book_id)
        if key in used:
            key = f"{key}-{book.book_id}"
        used[key] = book.book_id
        book.notes.append(key)


def video_target_name(book: BookPlan, kind: str) -> str:
    key = book.notes[-1] if book.notes else safe_filename(book.title, book.book_id)
    if kind == "interactive":
        return f"{key}+交互演示视频.mp4"
    return f"{key}+非交互视频.mp4"


# --------------------------------------------------------------------------- 源码包计划
# 规则顺序有意义：先命中的先算。
EXCLUSION_RULES: list[tuple[str, str, str]] = [
    ("secret", "密钥文件", ".env 与 .env.*（保留 .env.example 模板；密钥绝不入库）"),
    ("runs", "运行产物", "runs/ 下的视频、日志、浏览器 profile 等，可由流程重新生成"),
    ("reader", "reader 产物", "books/*/reader/ 下的分层图、动图、音频、reader/book.json，体积大且可复现"),
    ("pages", "绘本页图", "books/*/pages/ 下的扫描页图（二进制、版权归命题组，不随源码再分发）"),
    ("assets", "官方样书页图", "assets/book1.jpg、assets/book2/ 为官方样书页图，版权归命题组"),
    ("build", "构建/临时目录", ".tmp/、dist/、.git/、__pycache__/ 等"),
    ("archive", "归档/二进制产物", "*.zip、*.mp4、*.mov、*.pyc、*.key 等成品或密钥"),
]


def exclusion_reason(rel: str) -> str | None:
    """返回命中的排除原因；None 表示纳入。rel 使用 / 分隔。"""
    parts = rel.split("/")
    name = parts[-1]
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return EXCLUSION_RULES[0][2]
    if parts and parts[0] in ("runs", ".tmp", "dist", ".git"):
        return EXCLUSION_RULES[1][2] if parts[0] == "runs" else EXCLUSION_RULES[5][2]
    if len(parts) >= 3 and parts[0] == "books" and parts[2] == "reader":
        return EXCLUSION_RULES[2][2]
    if len(parts) >= 3 and parts[0] == "books" and parts[2] == "pages":
        return EXCLUSION_RULES[3][2]
    if rel == "assets/book1.jpg" or (len(parts) >= 2 and parts[0] == "assets" and parts[1] == "book2"):
        return EXCLUSION_RULES[4][2]
    if "__pycache__" in parts:
        return EXCLUSION_RULES[5][2]
    if name.lower().endswith((".zip", ".mp4", ".mov", ".pyc", ".key")):
        return EXCLUSION_RULES[6][2]
    return None


@dataclass
class SourcePlan:
    included: list[tuple[str, int]] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)
    manifest: str = ""
    tracked: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(size for _rel, size in self.included)


def list_tracked_files(repo: Path) -> list[str]:
    """`git ls-files -z`。只收 git 跟踪文件，未跟踪的工作区改动不打包。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    except FileNotFoundError as exc:
        raise PackagingError("找不到 git，无法列源码文件（可用 --dry-run 前先装 git）") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise PackagingError(f"git ls-files 失败：{err[-1] if err else proc.returncode}")
    raw = proc.stdout.decode("utf-8", "surrogateescape")
    return [item for item in raw.split("\0") if item]


def _manifest_exclusion_table(plan: SourcePlan) -> str:
    counts: dict[str, int] = {}
    for _rel, reason in plan.excluded:
        counts[reason] = counts.get(reason, 0) + 1
    lines = ["| 规则 | 命中文件数 | 原因 |", "| --- | --- | --- |"]
    for key, label, reason in EXCLUSION_RULES:
        n = counts.get(reason, 0)
        if n:
            lines.append(f"| {label} | {n} | {reason} |")
    if not plan.excluded:
        lines.append("| （无） | 0 | 本次没有命中排除规则 |")
    return "\n".join(lines)


def build_source_plan(repo: Path, tracked: list[str] | None = None,
                      package: str = "") -> SourcePlan:
    files = list_tracked_files(repo) if tracked is None else list(tracked)
    plan = SourcePlan(tracked=len(files))
    for rel in sorted(set(files)):
        reason = exclusion_reason(rel)
        if reason is not None:
            plan.excluded.append((rel, reason))
            continue
        path = repo / rel
        if not path.is_file():
            plan.excluded.append((rel, "工作区缺失（已从 git 索引删除/未检出）"))
            continue
        try:
            size = path.stat().st_size
        except OSError:
            plan.excluded.append((rel, "读取失败"))
            continue
        plan.included.append((rel, size))

    included_lines = "\n".join(f"{size:>12}  {rel}" for rel, size in plan.included)
    plan.manifest = "\n".join([
        "Book2Real 项目源代码包 · SOURCE_MANIFEST",
        "=" * 60,
        f"提交命名：{package or '（未指定）'}",
        f"生成时间：{now_text()}",
        "收录范围：仅 `git ls-files` 跟踪的文件；未跟踪的工作区修改、.git 目录不在包内。",
        "",
        "一、纳入内容",
        "- 代码：book2real/、scripts/、tests/",
        "- 配置与依赖：configs/、pyproject.toml、requirements.txt、.env.example（只有变量名，无密钥）",
        "- 文档：README.md、docs/（含赛题说明与架构文档）",
        "- 绘本结构化配置：books/*/book.json、books/*/curation.json、books/*/ORDER.csv",
        "- 第三方离线运行时：vendor/（jieba/pypinyin 兜底数据，许可证以 upstream 为准）",
        "- 其他：LICENSE、samples/",
        "",
        "二、排除项及原因",
        _manifest_exclusion_table(plan),
        "",
        "说明：排除项都没有删除，仍完整保留在提交仓库中；这里排除只是为了控制源码包体积、",
        "避免把版权页图/生成产物/密钥打进可下载包。关键流程配置（book.json/curation.json）",
        "与全部代码、文档均已纳入，可直接审查。",
        "",
        "三、统计",
        f"- git 跟踪文件：{plan.tracked} 个",
        f"- 纳入：{len(plan.included)} 个，压缩前合计 {fmt_size(plan.total_bytes)}",
        f"- 排除：{len(plan.excluded)} 个",
        "",
        "四、纳入文件清单（大小 与 相对路径）",
        included_lines or "（无）",
        "",
        "五、未纳入内容如何复现",
        "- books/<id>/reader/**：python -m book2real analyze/layers/cast 重新生成",
        "- 视频：python -m book2real film --book books/<id>（见 README）",
        "- 密钥：按 .env.example 在本地填写，不入库、不入包",
        "",
    ])
    return plan


def write_source_zip(plan: SourcePlan, dest: Path, repo: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as zf:
        zf.writestr(MANIFEST_NAME, plan.manifest)
        for rel, _size in plan.included:
            zf.write(repo / rel, arcname=rel)


# --------------------------------------------------------------------------- 文档
def find_statement(repo: Path, out_dir: Path) -> Path | None:
    """项目说明书.pdf 的查找顺序；找不到就返回 None（绝不拿别的 PDF 冒充）。"""
    for p in (
        repo / STATEMENT_NAME,
        repo / "docs" / STATEMENT_NAME,
        out_dir / STATEMENT_NAME,
        repo / "dist" / STATEMENT_NAME,
    ):
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def _tracked_note(repo: Path) -> str:
    compliance = repo / "docs" / "COMPLIANCE.md"
    if compliance.is_file():
        return (
            "- 仓库内 `docs/COMPLIANCE.md` 已记录素材授权、第三方服务与儿童内容安全的合规说明；"
            "本清单不重复编造，具体条目以该文件为准。\n")
    return ""


def render_material_sources(parts: tuple[str, str, str], books: list[BookPlan],
                            repo: Path, out_dir: Path) -> str:
    school, dept, person = parts
    statement = find_statement(repo, out_dir)
    book_rows = "\n".join(
        f"| {b.title} | `{b.book_id}` | {b.pages or '—'} | 命题组提供的授权/脱敏纸质绘本样本 |"
        for b in books
    ) or "| （无） | — | — | — |"
    compliance_note = _tracked_note(repo)
    return f"""# 素材来源清单

> 提交包：{package_name(parts)}（{school} / {dept} / {person}）
> 生成时间：{now_text()}
> 依据：`docs/纸书智伴.pdf` 第 3–4 页「材料提交」第 d 项；`docs/答疑/答疑总结.md` §3.7（Q31：知识产权/数据承诺写进项目说明书）。
> 配套文件：同目录 `知识产权承诺书.md`。

## 一、绘本图片与文本（输入素材）

| 绘本名称 | book_id | 页数 | 来源 |
| --- | --- | --- | --- |
{book_rows}

- 来源：命题组提供的 **6 本授权/脱敏纸质绘本样本**（见 `docs/纸书智伴.pdf`「命题组提供」一节）；本项目不另行采集未授权绘本。
- 存放：导入后的页图在仓库 `books/<book_id>/pages/`，只在本地生成流程使用；**不随「项目源代码.zip」分发**（见包内 `SOURCE_MANIFEST.txt`）。
- 版权：页图与原文版权归原出版方/命题组所有，本项目仅在本次赛题评审范围内使用，不再分发、不用于商业用途。
- 文本：正文由 OCR/VLM 识别后经人工校对，页码/分屏/热点等结构化结果落在 `books/<book_id>/book.json`、`curation.json`，属本项目整理成果并随源码包提交。

## 二、图片、音频与视频

- 角色切件、分层、动图与成片均由上述原书页图派生（`book2real/cast`、`layers`、`film` 链路），未引入第三方插画库。
- 旁白/问答语音由 TTS 合成（`edge-tts` 或本机语音，按 `book2real` 配置）；未使用外部录音素材。
- 未主动引入第三方音乐、音效、字体文件；如后续加入，必须在本节登记来源与许可证。
- 文档截图（`docs/images/`）为本项目自摄/自生成。

## 三、模型与算法

- 本地开源模型（可选）：`torch`、`torchvision`、`diffusers`、`transformers`、`accelerate`、`safetensors` 等，版本见 `requirements.txt`；权重运行时获取，不随包分发，许可证以各模型卡为准。
- 第三方闭源 API（若启用）：通过根目录 `.env` 的 `IMAGE_API_*` / `VIDEO_API_*` 配置，变量名见 `.env.example`（**不含密钥**）；实际供应商、模型名、调用范围需按本次提交如实填写，调用边界见 `docs/API_KEYS.md`。
- OCR/分词：`rapidocr_onnxruntime`、`jieba`、`pypinyin`（后者已 vendored 到 `vendor/` 供离线演示）。

## 四、工具与第三方代码

- Python 标准库与 `requirements.txt` 所列开源依赖。
- `vendor/jieba`、`vendor/pypinyin`：上游开源库的离线数据副本，许可证以 upstream 为准。
- `edge-tts`：GPL-3.0，仅用于评测/演示。
- 视频编码使用本机 `ffmpeg`（系统工具，未随包分发；许可证以本机安装版本为准）。
- 本项目自研代码均为团队原创，随「项目源代码.zip」提交。

## 五、合规说明

- 儿童内容安全：遵守《未成年人保护法》《未成年人网络保护条例》及网信办相关指南，按 `docs/答疑/答疑总结.md` §3.7 执行审核与复核。
{compliance_note}- 项目说明书：{"已随包提交（" + STATEMENT_NAME + "）" if statement else "**缺失**，需补齐后重新打包"}。

## 六、提交前需人工核对

- [ ] 第三方闭源服务的实际供应商/模型/调用范围（若本次使用）
- [ ] 任何新增字体、音乐、音效或外部数据集的来源与许可证
- [ ] 儿童内容安全复核责任人与联系方式（写入知识产权承诺书）
"""


def render_ip_commitment(parts: tuple[str, str, str], repo: Path) -> str:
    school, dept, person = parts
    compliance_note = _tracked_note(repo)
    return f"""# 知识产权承诺书

提交单位：{school}　{dept}
承诺人：{person}
提交包：{package_name(parts)}
生成时间：{now_text()}

本人及团队就参加本次企业预选赛提交的材料，郑重承诺：

## 一、素材来源真实、完整

1. 随包提交的《素材来源清单.md》真实、完整，逐项说明绘本图片、文本、音频、视频、模型、工具与第三方代码的来源。
2. 绘本页图与原文来自命题组提供的授权/脱敏样本，仅在本次赛题评审范围内使用；不对外传播、不再分发、不用于商业用途。
3. 未使用未授权的绘本、图片、音乐、音效、字体、声音或数据集；使用的开源模型/库遵守其许可证，并在清单中列明。
4. 如使用第三方闭源服务，已在清单中说明调用范围（服务商、模型、用途、数据边界）；不上传超出赛题必需的素材。

## 二、生成内容与可复现

5. 提交的视频、图像与文本由本项目流程生成；未使用赛前预先固定的结果冒充现场实时生成。
6. 关键生成内容可复现：源代码、配置、`books/*/book.json` 与 `curation.json` 随「项目源代码.zip」提交；为控制体积排除的页图/reader 产物及原因写在包内 `SOURCE_MANIFEST.txt`，可按文档重新生成。

## 三、儿童内容安全

7. 遵守《中华人民共和国未成年人保护法》《未成年人网络保护条例》及网信办相关指南，对生成内容做适龄标注与人工抽检。
8. 内容安全复核责任人：【待填：姓名 / 联系方式】；审核机制：【待填：审核流程与留痕方式】。

## 四、责任承担

9. 如因素材授权、来源披露不实或内容违规引发纠纷，由承诺人及团队承担相应责任，并配合组委会核查。

{compliance_note}
签署：{person}　　日期：______年____月____日

> 注：本承诺书为脚本生成的模板，签署前请补齐【待填】项并手写/电子签名。
"""


def _rel(path: Path | None, repo: Path) -> str:
    if path is None:
        return "—"
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path)


def render_readme(package: str, parts: tuple[str, str, str], target: Path,
                  books: list[BookPlan], statement: Path | None,
                  source_size: int, repo: Path) -> str:
    school, dept, person = parts
    rows: list[str] = []

    def row(name: str, status: str, size: int | None, src: str) -> None:
        rows.append(f"| `{name}` | {status} | {fmt_size(size) if size is not None else '—'} | {src} |")

    st = target / STATEMENT_NAME
    if st.is_file():
        row(STATEMENT_NAME, "就绪", st.stat().st_size, f"`{_rel(statement, repo)}`")
    else:
        row(STATEMENT_NAME, "**缺失**", None, "未找到；放到仓库根或 docs/ 后重跑")
    row(SOURCE_ZIP_NAME, "就绪" if (target / SOURCE_ZIP_NAME).is_file() else "**缺失**",
        (target / SOURCE_ZIP_NAME).stat().st_size if (target / SOURCE_ZIP_NAME).is_file() else None,
        "git 跟踪文件，排除项见包内 SOURCE_MANIFEST.txt")
    for book in books:
        for kind in ("film", "interactive"):
            name = video_target_name(book, kind)
            path = target / name
            src = book.interactive if kind == "interactive" else book.film
            if path.is_file():
                row(name, "就绪", path.stat().st_size, f"`{_rel(src, repo)}`")
            else:
                label = "交互演示" if kind == "interactive" else "非交互"
                row(name, "**缺失**", None, f"需要录制/生成{label}视频")
    for name in (MATERIAL_NAME, IP_NAME):
        p = target / name
        row(name, "就绪" if p.is_file() else "**缺失**",
            p.stat().st_size if p.is_file() else None, "脚本生成")

    missing = []
    if not (target / STATEMENT_NAME).is_file():
        missing.append("- 缺失：项目说明书.pdf（放到仓库根或 `docs/` 下后重跑）")
    for book in books:
        if book.film is None:
            missing.append(f"- 缺失：{book.title} 的非交互视频（`runs/{book.book_id}/reader_film/*/film.mp4`）")
        if book.interactive is None:
            missing.append(
                "- 缺失：需要录制交互演示"
                f"（{book.title} / {book.book_id}）：把录屏放到 "
                f"`runs/{book.book_id}/interactive.mp4` 或 "
                f"`runs/{book.book_id}/reader_film/<时间戳>/interactive.mp4`")
    if not missing:
        missing.append("- 无；四份官方材料均已就绪。")

    volume = [f"- 项目源代码.zip：{fmt_size(source_size)}（上限 2GB）",
              f"- 最终压缩包：`{package}.zip`（见输出目录，上限 2GB）"]
    big = [p for p in target.iterdir() if p.is_file() and p.stat().st_size > WARN_FILE_BYTES]
    volume.append("- 单文件 >500MB：" + ("、".join(p.name for p in big) if big else "无"))

    return f"""# 提交包说明 · {package}

- 学校：{school}
- 院系：{dept}
- 姓名：{person}
- 生成时间：{now_text()}

官方要求四份材料（`docs/答疑/答疑总结.md` §3.1）：项目说明书.pdf、项目源代码.zip、
`<绘本名称>+非交互视频.mp4`、`<绘本名称>+交互演示视频.mp4`；整个目录再压缩为
`{package}.zip`。另按 `docs/纸书智伴.pdf` 第 3–4 页附素材来源清单与知识产权承诺书。

## 一、文件清单

| 文件 | 状态 | 大小 | 来源 |
| --- | --- | --- | --- |
{chr(10).join(rows)}

## 二、缺失 / 待补（提交前必须处理）

{chr(10).join(missing)}

> 交互演示视频需要人工录屏（点击目标→暂停→展示介绍、语音问答等），脚本不会用非交互视频冒充。

## 三、体积检查

{chr(10).join(volume)}

## 四、重新生成

```bash
python scripts/package_submission.py --name "{package}" --out dist
```

- `--dry-run` 只打印计划与体积，不写盘。
- 要换书/运行目录：`--books books --runs runs`。
- 源码包只收 git 跟踪文件；`.env*`（`.env.example` 除外）、`runs/`、
  `books/*/reader/`、`books/*/pages/`、`assets/book2/` 等已在包内 `SOURCE_MANIFEST.txt` 说明原因。
"""


# --------------------------------------------------------------------------- 主流程
def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _check_limits(files: list[Path], report: Report, label: str,
                  limit: int = MAX_ZIP_BYTES) -> None:
    for p in files:
        if not p.is_file():
            continue
        size = p.stat().st_size
        if size > limit:
            report.fail(f"{label} {p.name} 超过上限 {fmt_size(limit)}（实际 {fmt_size(size)}）")
        if size > WARN_FILE_BYTES:
            report.warn(f"单文件超过 500MB：{p.name}（{fmt_size(size)}）")


def _zip_dir(src_dir: Path, zip_path: Path, package: str) -> None:
    """把 <out>/<name>/ 整个目录压成 <out>/<name>.zip（视频/PDF/内层 zip 用 STORED）。"""
    stored = (".mp4", ".mov", ".zip", ".pdf", ".jpg", ".jpeg", ".png", ".gif")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as zf:
        for p in sorted(src_dir.rglob("*")):
            if not p.is_file():
                continue
            arc = (Path(package) / p.relative_to(src_dir)).as_posix()
            comp = zipfile.ZIP_STORED if p.suffix.lower() in stored else zipfile.ZIP_DEFLATED
            zf.write(p, arcname=arc, compress_type=comp)


def run(args: argparse.Namespace, repo: Path, report: Report,
        tracked: list[str] | None = None) -> int:
    try:
        parts = parse_name(args.name)
    except ValueError as exc:
        print(f"错误：{exc}")
        print('用法：python scripts/package_submission.py --name "学校-院系-姓名" '
              "--out dist [--books books] [--runs runs] [--dry-run]")
        return 2
    package = package_name(parts)
    report.ok(f"命名解析：{parts[0]} / {parts[1]} / {parts[2]} → {package}")

    out_dir = Path(args.out).expanduser()
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    out_dir = out_dir.resolve()
    books_dir = Path(args.books).expanduser() if args.books else repo / "books"
    runs_dir = Path(args.runs).expanduser() if args.runs else repo / "runs"
    if not books_dir.is_absolute():
        books_dir = (Path.cwd() / books_dir).resolve()
    if not runs_dir.is_absolute():
        runs_dir = (Path.cwd() / runs_dir).resolve()
    target = out_dir / package
    final_zip = out_dir / f"{package}.zip"

    print(f"提交包 · {package}")
    print(f"仓库：{repo}")
    print(f"输出：{target}{'（演练，不写盘）' if args.dry_run else ''}")
    print(f"绘本：{books_dir}    运行记录：{runs_dir}")

    books = load_books(books_dir, report)
    plan_videos(books, runs_dir, report)

    try:
        source = build_source_plan(repo, tracked=tracked, package=package)
    except PackagingError as exc:
        report.fail(str(exc))
        source = SourcePlan()
    if source.included:
        report.ok(f"源码计划：{len(source.included)} 个文件，压缩前 {fmt_size(source.total_bytes)}"
                  f"（排除 {len(source.excluded)} 个）")
    elif not source.included and source.tracked == 0:
        report.fail("没有可打包的 git 跟踪文件")
    if source.total_bytes > MAX_ZIP_BYTES:
        report.fail(f"项目源代码.zip 压缩前已超过上限 {fmt_size(MAX_ZIP_BYTES)}"
                    f"（{fmt_size(source.total_bytes)}）")

    statement = find_statement(repo, out_dir)
    if statement is None:
        report.warn(f"缺失：{STATEMENT_NAME}（放到仓库根或 docs/ 下后重跑；脚本不会拿别的 PDF 冒充）")
    else:
        report.ok(f"项目说明书：{statement}（{fmt_size(statement.stat().st_size)}）")

    material = render_material_sources(parts, books, repo, out_dir)
    ip_doc = render_ip_commitment(parts, repo)

    if args.dry_run:
        print("\n[计划] 文件")
        rows: list[tuple[str, str, int | None]] = []
        rows.append((STATEMENT_NAME, "就绪" if statement else "缺失",
                     statement.stat().st_size if statement else None))
        rows.append((SOURCE_ZIP_NAME, "计划生成（压缩前）", source.total_bytes))
        for book in books:
            for kind, path in (("film", book.film), ("interactive", book.interactive)):
                name = video_target_name(book, kind)
                rows.append((name, "就绪" if path else "缺失",
                             path.stat().st_size if path else None))
        for name, text in ((MATERIAL_NAME, material), (IP_NAME, ip_doc)):
            rows.append((name, "计划生成", len(text.encode("utf-8"))))
        rows.append((README_NAME, "计划生成", None))
        for name, status, size in rows:
            print(f"  {'ok ' if status == '就绪' else '  '}{name:<40} {status:<22} "
                  f"{fmt_size(size) if size is not None else '—'}")
        total = source.total_bytes
        for book in books:
            for path in (book.film, book.interactive):
                if path is not None:
                    total += path.stat().st_size
        if statement is not None:
            total += statement.stat().st_size
        candidates = [statement, *(b.film for b in books), *(b.interactive for b in books)]
        _check_limits([p for p in candidates if p is not None], report, "输入文件")
        print(f"\n[体积] 源码压缩前 {fmt_size(source.total_bytes)}；"
              f"整包预估（未压缩/未计内层 zip 压缩率）{fmt_size(total)}；上限 {fmt_size(MAX_ZIP_BYTES)}")
        print(f"[体积] 最终压缩包：{final_zip.name}（实跑后生成）")
        report.ok("演练模式：未创建任何目录或文件")
    else:
        built = False
        if target.exists():
            report.fail(f"输出目录已存在：{target}；请先删除（或换 --out）再打包，避免混入旧文件")
        elif final_zip.exists():
            report.fail(f"输出压缩包已存在：{final_zip}；请先删除再打包")
        else:
            try:
                target.mkdir(parents=True)
                if statement is not None:
                    shutil.copyfile(statement, target / STATEMENT_NAME)
                write_source_zip(source, target / SOURCE_ZIP_NAME, repo)
                report.ok(f"已生成 {SOURCE_ZIP_NAME}（{fmt_size((target / SOURCE_ZIP_NAME).stat().st_size)}）")
                for book in books:
                    for kind, path in (("film", book.film), ("interactive", book.interactive)):
                        if path is None:
                            continue
                        dest = target / video_target_name(book, kind)
                        shutil.copyfile(path, dest)
                        report.ok(f"已复制 {dest.name}（{fmt_size(dest.stat().st_size)}）")
                _write_text(target / MATERIAL_NAME, material)
                _write_text(target / IP_NAME, ip_doc)
                report.ok(f"已生成 {MATERIAL_NAME}、{IP_NAME}")
                _write_text(target / README_NAME, render_readme(
                    package, parts, target, books, statement,
                    (target / SOURCE_ZIP_NAME).stat().st_size, repo))
                report.ok(f"已生成 {README_NAME}")
                built = True
            except OSError as exc:
                report.fail(f"写盘失败：{exc}")
                shutil.rmtree(target, ignore_errors=True)   # 清掉半成品，允许重跑

        if built:
            _check_limits([p for p in target.iterdir() if p.is_file()], report, "输出文件")
            try:
                _zip_dir(target, final_zip, package)
                size = final_zip.stat().st_size
                if size > MAX_ZIP_BYTES:
                    report.fail(f"最终压缩包超过上限 {fmt_size(MAX_ZIP_BYTES)}（实际 {fmt_size(size)}）")
                else:
                    report.ok(f"已生成 {final_zip.name}（{fmt_size(size)}）")
            except OSError as exc:
                report.fail(f"压缩最终包失败：{exc}")

    print()
    for ok, msg in report.checks:
        print(("ok    " if ok else "失败  ") + msg)
    for warning in report.warnings:
        print("警告  " + warning)
    print(f"结果：{report.passed} 通过，{report.failed} 失败")
    return 0 if report.failed == 0 else 1


def main(argv: list[str] | None = None, repo: Path | str | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Book2Real 企业预选赛提交包打包器",
        epilog='示例：python scripts/package_submission.py --name "国科大-计算机-段柏旸" --out dist --dry-run')
    parser.add_argument("--name", required=True,
                        help="学校-院系-姓名（用 - 或 _ 分隔三段）")
    parser.add_argument("--out", default="dist", help="输出目录（默认 dist）")
    parser.add_argument("--books", default=None, help="绘本目录（默认 <repo>/books）")
    parser.add_argument("--runs", default=None, help="运行记录目录（默认 <repo>/runs）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与体积，不写盘")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 被重定向到 StringIO/老控制台时忽略
        pass
    report = Report()
    return run(args, Path(repo or REPO).resolve(), report)


if __name__ == "__main__":
    raise SystemExit(main())
