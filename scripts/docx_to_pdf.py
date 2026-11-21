"""用本机 Word（COM）把 .docx 另存为 .pdf。

    python scripts/docx_to_pdf.py docs/a.docx [docs/b.docx ...]
    python scripts/docx_to_pdf.py docs/a.docx --out-dir .tmp/pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import win32com.client


def convert_one(word, src: Path, dst: Path) -> Path:
    try:
        doc = word.Documents.Open(str(src.resolve()), ReadOnly=False, AddToRecentFiles=False)
    except Exception:
        # 个别路径 Word 会“打开失败”，复制到临时路径再开（内容不变）
        import shutil
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="docx2pdf_"))
        tmp_src = tmp_dir / "input.docx"
        shutil.copy2(src, tmp_src)
        doc = word.Documents.Open(str(tmp_src.resolve()), ReadOnly=False, AddToRecentFiles=False)
    try:
        doc.SaveAs(str(dst.resolve()), FileFormat=17)  # 17 = wdFormatPDF
    finally:
        doc.Close(SaveChanges=0)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("docx", nargs="+")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    failures = 0
    try:
        for raw in args.docx:
            src = Path(raw)
            if not src.exists():
                print(f"missing: {src}")
                failures += 1
                continue
            out_dir = Path(args.out_dir) if args.out_dir else src.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            dst = out_dir / (src.stem + ".pdf")
            try:
                convert_one(word, src, dst)
                print(f"ok: {src} -> {dst} ({dst.stat().st_size} bytes)")
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL: {src}: {exc!r}")
                failures += 1
    finally:
        word.Quit()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
