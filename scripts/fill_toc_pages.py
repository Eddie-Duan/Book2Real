"""给说明书目录补页码（页码取自已生成的 PDF），然后在调用方重新导出 PDF。

用法：python scripts/fill_toc_pages.py [--docx docs/项目说明书.docx] [--pdf docs/项目说明书.pdf]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import docx
import pymupdf
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.shared import Cm

ENTRIES = [
    "1 摘要", "2 需求分析", "3 系统架构", "4 数据与模型来源",
    "5 主要功能", "6 关键实现与方法", "7 测试结果", "8 局限性与风险",
    "9 规范合规与伦理", "附录",
]


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def page_of(pdf_pages: list[str], label: str, start: int = 0) -> int | None:
    target = norm(label) if label != "附录" else "附录A"
    for i in range(start, len(pdf_pages)):
        if target in norm(pdf_pages[i]):
            return i + 1
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docx", default="docs/项目说明书.docx")
    ap.add_argument("--pdf", default="docs/项目说明书.pdf")
    args = ap.parse_args()

    doc_path = Path(args.docx)
    pages = [p.get_text() or "" for p in pymupdf.open(args.pdf)]
    # 跳过封面与目录页：从"目录"之后开始找
    start = 0
    for i, text in enumerate(pages[:4]):
        if "目录" in norm(text):
            start = i + 1
            break

    d = docx.Document(str(doc_path))
    seen = set()
    filled = 0
    for p in d.paragraphs:
        text = p.text.strip()
        if text in ENTRIES and text not in seen:
            seen.add(text)
            page = page_of(pages, text, start)
            if page is None:
                continue
            # 已有页码就替换掉
            for run in p.runs[1:]:
                run._element.getparent().remove(run._element)
            first = p.runs[0]
            first.text = text
            tab_run = p.add_run("\t" + str(page))
            tab_run.font.size = first.font.size
            tab_run.font.name = first.font.name
            pf = p.paragraph_format
            pf.tab_stops.add_tab_stop(Cm(16.0), WD_TAB_ALIGNMENT.RIGHT, leader=1)  # 1 = 点线
            filled += 1
    d.save(str(doc_path))
    print(f"toc entries filled: {filled} / {len(ENTRIES)}")


if __name__ == "__main__":
    main()
