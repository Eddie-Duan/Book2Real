"""把 .docx 里所有 "____" 占位与常见待办词加黄色高亮，便于人眼确认。

用法：
    python scripts/highlight_docx_blanks.py docs/项目说明书.docx           # 就地修改（先写 .bak）
    python scripts/highlight_docx_blanks.py docs/项目说明书.docx --dry-run  # 只报告命中数

只改格式（w:highlight），不动文字；对同一段落里夹杂的 "____" 会拆成独立 run，
避免整段变黄。
"""

from __future__ import annotations

import argparse
import copy
import re
import shutil
from pathlib import Path

import docx
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml.ns import qn

BLANK_RE = re.compile(r"_{3,}")
TODO_WORDS = ("待定", "待补", "待填", "【待确认】", "【待填】")


def _highlight_run(run, color=WD_COLOR_INDEX.YELLOW) -> None:
    run.font.highlight_color = color
    rpr = run._element.get_or_add_rPr()
    if rpr.find(qn("w:highlight")) is None:
        el = rpr.makeelement(qn("w:highlight"), {qn("w:val"): "yellow"})
        rpr.append(el)


def _split_run_on_blanks(run) -> int:
    """把含 "____" 的 run 拆成 多个 run，只给占位加高亮。返回命中数。"""
    text = run.text
    parts = BLANK_RE.split(text)
    if len(parts) == 1:
        return 0
    base = run._element
    rpr = base.find(qn("w:rPr"))
    parent = base.getparent()
    index = list(parent).index(base)
    new_elements = []
    for i, part in enumerate(parts):
        if part:
            el = base.makeelement(qn("w:r"), {})
            if rpr is not None:
                el.append(copy.deepcopy(rpr))
            t = el.makeelement(qn("w:t"), {qn("xml:space"): "preserve"})
            t.text = part
            el.append(t)
            new_elements.append(el)
        if i < len(parts) - 1:
            el = base.makeelement(qn("w:r"), {})
            if rpr is not None:
                el.append(copy.deepcopy(rpr))
            t = el.makeelement(qn("w:t"), {qn("xml:space"): "preserve"})
            t.text = "____"
            el.append(t)
            from docx.text.run import Run
            new_elements.append(el)
    for k, el in enumerate(new_elements):
        parent.insert(index + k, el)
    parent.remove(base)
    # 重新加高亮
    added = 0
    for k, el in enumerate(new_elements):
        from docx.text.run import Run
        r = Run(el, run._parent)
        if BLANK_RE.fullmatch(r.text or ""):
            _highlight_run(r)
            added += 1
    return added


def process_paragraphs(paragraphs, dry_run: bool) -> int:
    hits = 0
    for p in paragraphs:
        if not p.text.strip():
            continue
        if "____" in p.text:
            if dry_run:
                hits += len(BLANK_RE.findall(p.text))
            else:
                for run in list(p.runs):
                    if "____" in run.text:
                        hits += _split_run_on_blanks(run)
        for word in TODO_WORDS:
            if word in p.text:
                for run in p.runs:
                    if word in (run.text or ""):
                        hits += 1
                        if not dry_run:
                            _highlight_run(run)
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.path)
    doc = docx.Document(str(path))
    hits = process_paragraphs(doc.paragraphs, args.dry_run)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                hits += process_paragraphs(cell.paragraphs, args.dry_run)

    if args.dry_run:
        print(f"{path}: would highlight {hits} run(s)/blank(s)")
        return

    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    doc.save(str(path))
    print(f"{path}: highlighted {hits} run(s)/blank(s); backup at {backup}")


if __name__ == "__main__":
    main()
