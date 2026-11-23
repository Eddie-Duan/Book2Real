#!/usr/bin/env python3
"""book2real 阅读包 -> skills 产线上游（page 目录四件套 + 真实质量指标）。

skills/ 产线的每个页面目录必须有四个文件：
    clean_keyframe.png     去字后的整页关键帧
    page_manifest.json     page_id（必须等于目录名）/ 场景 / 角色
    qa_report.json         必须带 verdict=PASS 且 ocr_residual_count、
                           non_edited_ssim、protected_zone_intrusion_px 三项
                           （preflight 会读这三个键，缺一个就阻断）
    seedance_prompt.txt    带风格 DNA 的画面提示词

本脚本做的是 S1（picturebook-to-seedance）的本地实现：
    原文页图 + OCR 文字框 → S1 的笔画级掩膜 → OpenCV Telea 修补
    → 掩膜外像素锁定（与 composite.py 同语义）→ 关键帧
    → 真实指标（残字计数 / 非编辑区 SSIM / 保护带入侵像素）

三项指标不是写死的常数：
    - ocr_residual_count：逐文字框统计修补后仍偏离局部背景的像素占比，
      超过阈值即计一条残留；
    - non_edited_ssim：掩膜外区域的 SSIM（掩膜外本应逐像素不变）；
    - protected_zone_intrusion_px：掩膜外被改动的像素数（像素锁保证为 0）。

用法：
    python skills/living-picturebook/scripts/prepare_from_book.py \
        --book books/cn01 --out .tmp/skill_pages [--max-width 1920]

不调用任何模型、不联网、不花钱。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

PROMPT_CHAR_LIMIT = 1000
LANG_NAME = {"zh": "中文", "en": "英文"}
RESIDUAL_INK_THRESHOLD = 0.03     # 单框内残留墨迹占比超过它就算一条残留
PROTECTED_INTRUSION_MAX = 0      # 像素锁下必须为 0
SSIM_MIN = 0.95

DEFAULT_STYLE_DNA = (
    "儿童绘本插画风格；严格保持原书的画风、笔触、配色与光线；"
    "构图与主体比例沿用参考图；画面内不要出现任何文字、字幕、水印或标识；"
    "镜头平稳，适合 5 秒短片。"
)

S1_SCRIPT = Path(__file__).resolve().parents[2] / "picturebook-to-seedance" / "scripts" / "make_text_mask.py"


def load_book(book_dir: Path) -> dict:
    for rel in ("reader/book.json", "book.json"):
        p = book_dir / rel
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise SystemExit(f"[prepare] {book_dir} 下找不到 reader/book.json")


def page_image(book_dir: Path, page: dict, prefer_art: bool) -> tuple[Path, str]:
    art = page.get("art")
    if prefer_art and art:
        p = book_dir / "reader" / art
        if p.is_file():
            return p, "art"
    pid = page.get("id")
    for ext in (".jpg", ".png", ".jpeg"):
        p = book_dir / "pages" / f"{pid}{ext}"
        if p.is_file():
            return p, "page"
    raise SystemExit(f"[prepare] 页 {pid} 找不到图片")


def boxes_in_bounds(boxes: list, w: int, h: int, tol: float = 0.01) -> bool:
    for b in boxes:
        if not isinstance(b, (list, tuple)) or len(b) != 4:
            return False
        x0, y0, x1, y1 = (float(v) for v in b)
        if x1 < x0 or y1 < y0:
            return False
        if not (-w * tol <= x0 <= w * (1 + tol)) or not (-w * tol <= x1 <= w * (1 + tol)):
            return False
        if not (-h * tol <= y0 <= h * (1 + tol)) or not (-h * tol <= y1 <= h * (1 + tol)):
            return False
    return True


def to_norm_boxes(boxes: list, w: int, h: int) -> list:
    """像素框 [x0,y0,x1,y1] -> 0-1000 归一 [ymin,xmin,ymax,xmax]（make_text_mask 契约）。"""
    out = []
    for b in boxes:
        x0, y0, x1, y1 = (float(v) for v in b)
        out.append([round(y0 / h * 1000, 2), round(x0 / w * 1000, 2),
                    round(y1 / h * 1000, 2), round(x1 / w * 1000, 2)])
    return out


def make_mask(img_path: Path, blocks_json: Path, mask_path: Path, dilate: int = 6) -> bool:
    """调用 S1 的笔画级掩膜脚本；失败返回 False（调用方回退到整框掩膜）。"""
    if not S1_SCRIPT.is_file():
        return False
    cmd = [sys.executable, str(S1_SCRIPT), "--image", str(img_path),
           "--blocks", str(blocks_json), "--out", str(mask_path), "--dilate", str(dilate)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and mask_path.is_file()


def fallback_mask(boxes: list, w: int, h: int) -> np.ndarray:
    m = np.zeros((h, w), dtype=np.uint8)
    for x0, y0, x1, y1 in boxes:
        m[max(0, int(y0)):min(h, int(y1)), max(0, int(x0)):min(w, int(x1))] = 255
    return m


def inpaint_clean(orig: np.ndarray, mask: np.ndarray, radius: int = 3) -> np.ndarray:
    """Telea 修补 + 掩膜外像素锁定。"""
    import cv2

    gray = cv2.cvtColor(orig, cv2.COLOR_RGB2GRAY)
    filled = cv2.inpaint(gray, mask, radius, cv2.INPAINT_TELEA)
    filled_rgb = np.dstack([filled, filled, filled])
    html = mask > 0
    out = orig.copy()
    out[html] = filled_rgb[html]
    return out


def ink_residual(box: list, gray: np.ndarray, thresh: float) -> float:
    """框内「偏离局部背景」的像素占比，用来判断字有没有被修掉。"""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    h, w = gray.shape
    x0, x1 = max(0, min(x0, w - 1)), max(0, min(x1, w))
    y0, y1 = max(0, min(y0, h - 1)), max(0, min(y1, h))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return 0.0
    patch = gray[y0:y1, x0:x1].astype(np.float32)
    med = float(np.median(patch))
    frac = float(np.mean(np.abs(patch - med) > 36))  # 36/255≈14% 的局部反差
    return frac


def non_edited_ssim(orig: np.ndarray, out: np.ndarray, mask: np.ndarray) -> float:
    """掩膜外（未编辑区）的 SSIM；掩膜外逐像素应完全相同。"""
    from skimage.metrics import structural_similarity as ssim

    keep = mask == 0
    if keep.sum() < 1000:
        return 1.0
    ga = np.asarray(Image.fromarray(orig).convert("L"))
    gb = np.asarray(Image.fromarray(out).convert("L"))
    a = np.where(keep, ga, 0).astype(np.uint8)
    b = np.where(keep, gb, 0).astype(np.uint8)
    return float(ssim(a, b, data_range=255))


def faint_text_hits(img: np.ndarray, *, min_len: int = 4,
                    min_score: float = 0.6) -> list[dict]:
    """Read text that the raw-page OCR misses: watermarks and 10–20% alpha
    overlays only become readable after the local background is subtracted.

    Returns ``[{"box": [x0,y0,x1,y1], "text": str, "score": float}]`` in the
    pixel coordinates of ``img``.
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
        import cv2
    except Exception:
        return []
    global _OCR
    try:
        _OCR
    except NameError:
        _OCR = RapidOCR()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    bg = cv2.GaussianBlur(gray, (0, 0), 15)
    resid = np.clip(bg.astype(np.int16) - gray.astype(np.int16), 0, 60).astype(np.uint8)
    enh = cv2.normalize(resid, None, 0, 255, cv2.NORM_MINMAX)
    res, _ = _OCR(enh)
    out: list[dict] = []
    for item in res or []:
        pts, text, score = item[0], str(item[1] or ""), float(item[2])
        text = text.strip()
        if len(text) < min_len or score < min_score:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        out.append({"box": [float(min(xs)), float(min(ys)),
                            float(max(xs)), float(max(ys))],
                    "text": text, "score": score})
    return out


def merge_boxes(a: list, b: list, *, iou_max: float = 0.2) -> list[list[float]]:
    """Union of two box lists, dropping b-boxes that merely re-cover an a-box."""

    def iou(p, q) -> float:
        ix0, iy0 = max(p[0], q[0]), max(p[1], q[1])
        ix1, iy1 = min(p[2], q[2]), min(p[3], q[3])
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        inter = (ix1 - ix0) * (iy1 - iy0)
        union = (p[2] - p[0]) * (p[3] - p[1]) + (q[2] - q[0]) * (q[3] - q[1]) - inter
        return inter / union if union > 0 else 0.0

    out = list(a)
    for q in b:
        if all(iou(p, q) < iou_max for p in a):
            out.append(q)
    return out


def ocr_residual_count(img: np.ndarray, boxes: list, sx: float, sy: float) -> tuple[int, list]:
    """在清字后的关键帧上跑本地 OCR，统计仍能读出文字、且与原文字框重叠的条数。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        return -1, []          # -1 = 本机没有 OCR，指标不可用

    global _OCR
    try:
        _OCR
    except NameError:
        _OCR = RapidOCR()

    res, _ = _OCR(img)
    hits = []
    for item in res or []:
        pts, text, score = item[0], item[1], float(item[2])
        if not text or len(text.strip()) < 2 or score < 0.5:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        # 回到源页坐标系
        sx0, sy0, sx1, sy1 = x0 / sx, y0 / sy, x1 / sx, y1 / sy
        for b in boxes:
            ix0, iy0 = max(sx0, b[0]), max(sy0, b[1])
            ix1, iy1 = min(sx1, b[2]), min(sy1, b[3])
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            inter = (ix1 - ix0) * (iy1 - iy0)
            area = (sx1 - sx0) * (sy1 - sy0)
            if inter / max(area, 1.0) > 0.2:
                hits.append({"text": text, "score": round(score, 3)})
                break
    return len(hits), hits


def pad_to_ratio(img: Image.Image, ratio: float, bg: tuple[int, int, int]) -> Image.Image:
    """把页图放进固定宽高比的画布（居中、留边），保证整本条段尺寸一致。"""
    w, h = img.size
    if abs(w / h - ratio) < 1e-3:
        return img
    if w / h > ratio:                      # 太宽：上下补边
        nh = round(w / ratio)
        canvas = Image.new("RGB", (w, nh), bg)
        canvas.paste(img, (0, (nh - h) // 2))
    else:                                   # 太高：左右补边
        nw = round(h * ratio)
        canvas = Image.new("RGB", (nw, h), bg)
        canvas.paste(img, ((nw - w) // 2, 0))
    return canvas


def edge_color(arr: np.ndarray) -> tuple[int, int, int]:
    """取页图四边像素的中位色当作补边底色（通常就是纸张色）。"""
    edge = np.concatenate([arr[0, :, :], arr[-1, :, :], arr[:, 0, :], arr[:, -1, :]], axis=0)
    med = np.median(edge, axis=0).astype(int)
    return tuple(int(v) for v in med)


def build_prompt(page: dict, lang: str, style_dna: str) -> str:
    body = (page.get("scene") or "").strip() or (page.get("text") or "").strip().replace("\n", " ")
    if not body:
        body = "画面沿用参考图的构图与主体。"
    prompt = (f"{style_dna}\n本页画面：{body}\n"
              f"旁白语言：{LANG_NAME.get(lang, '中文')}；画面本身不要出现文字。")
    return prompt[:PROMPT_CHAR_LIMIT]


def main() -> int:
    ap = argparse.ArgumentParser(description="book2real 阅读包 -> skills 产线页面目录")
    ap.add_argument("--book", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--style-dna", default=DEFAULT_STYLE_DNA)
    ap.add_argument("--max-width", type=int, default=1920)
    ap.add_argument("--aspect", default="16:9", help="关键帧统一宽高比（默认 16:9；none=不补边）")
    ap.add_argument("--prefer-art", action="store_true",
                    help="直接用 storybook 已清字的 art（不走本地修补，指标会按 art 计）")
    ap.add_argument("--pages", default="",
                    help="只处理这些页（逗号分隔，如 p04,p09）；默认全部")
    a = ap.parse_args()

    book_dir, out_dir = Path(a.book), Path(a.out)
    book = load_book(book_dir)
    book_id = book.get("book_id") or book_dir.name
    lang = (book.get("language") or "zh").lower()
    pages = book.get("pages") or []
    if not pages:
        raise SystemExit("[prepare] book.json 里没有 pages")
    if a.pages:
        wanted = {s.strip() for s in str(a.pages).split(",") if s.strip()}
        pages = [p for p in pages if (p.get("id") or "") in wanted]
        if not pages:
            raise SystemExit(f"[prepare] --pages {a.pages} 没有匹配到任何页")

    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []

    ratio = None
    if a.aspect != "none":
        num, den = a.aspect.split(":")
        ratio = float(num) / float(den)

    def finalize(arr: np.ndarray) -> Image.Image:
        im = Image.fromarray(arr)
        if ratio:
            im = pad_to_ratio(im, ratio, edge_color(arr))
        if im.size[0] > a.max_width:
            im = im.resize((a.max_width, round(im.size[1] * a.max_width / im.size[0])), Image.LANCZOS)
        return im

    for idx, page in enumerate(pages, start=1):
        pid = page.get("id") or f"p{idx:02d}"
        safe = "".join(c for c in pid if c.isalnum() or c in "-_") or f"p{idx:02d}"
        pdir = out_dir / safe
        pdir.mkdir(parents=True, exist_ok=True)

        img_path, src_tag = page_image(book_dir, page, prefer_art=a.prefer_art)
        orig_img = Image.open(img_path).convert("RGB")
        w0, h0 = orig_img.size
        orig = np.asarray(orig_img)

        boxes = page.get("text_boxes") or [(l.get("box") or []) for l in (page.get("ocr_lines") or [])]
        boxes = [[float(v) for v in b] for b in boxes if isinstance(b, (list, tuple)) and len(b) == 4]
        # Watermarks / faint overlays are invisible to the raw OCR pass; read them
        # on the background-subtracted image and mask them too.
        faint = faint_text_hits(orig)
        boxes = merge_boxes(boxes, [h["box"] for h in faint])

        blocks_json = pdir / "text_boxes.json"
        blocks_json.write_text(
            json.dumps({"text_blocks": to_norm_boxes(boxes, w0, h0)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        mask_png = pdir / "_mask.png"
        if boxes and make_mask(img_path, blocks_json, mask_png):
            mask = np.asarray(Image.open(mask_png).convert("L"))
        elif boxes:
            mask = fallback_mask(boxes, w0, h0)
        else:
            mask = np.zeros((h0, w0), dtype=np.uint8)

        if a.prefer_art or src_tag == "art":
            out = orig                      # art 已由 storybook 清过字
            mask_used = np.zeros((h0, w0), dtype=np.uint8)
            attempts = []
        else:
            # 依次加粗掩膜/修补半径，直到没有残字；记录用了几次
            attempts = []
            out = None
            for dilate, radius in ((6, 3), (10, 4), (14, 5), (20, 6)):
                if boxes:
                    make_mask(img_path, blocks_json, mask_png, dilate=dilate)
                    m = np.asarray(Image.open(mask_png).convert("L")) if mask_png.is_file() else fallback_mask(boxes, w0, h0)
                else:
                    m = np.zeros((h0, w0), dtype=np.uint8)
                cand = inpaint_clean(orig, m, radius=radius)
                g_cand = np.asarray(Image.fromarray(cand).convert("L"))
                left = sum(1 for b in boxes if ink_residual(b, g_cand, RESIDUAL_INK_THRESHOLD) > RESIDUAL_INK_THRESHOLD)
                attempts.append({"dilate": dilate, "radius": radius, "residual": left})
                out, mask_used = cand, m
                if left == 0:
                    break
            mask_png.unlink(missing_ok=True)

        # 指标（放在所有清理轮之后，避免用中间结果打分）
        res_count = 0
        if boxes:
            g = np.asarray(Image.fromarray(out).convert("L"))
            for b in boxes:
                if ink_residual(b, g, RESIDUAL_INK_THRESHOLD) > RESIDUAL_INK_THRESHOLD:
                    res_count += 1
        ssim_val = non_edited_ssim(orig, out, mask_used) if not a.prefer_art else 1.0
        intrusion = int(np.count_nonzero((out != orig).any(axis=2) & (mask_used == 0))) if not a.prefer_art else 0

        kf = finalize(out)
        kf.save(pdir / "clean_keyframe.png", format="PNG")
        mask_png.unlink(missing_ok=True)
        ocr_left, ocr_hits = (0, []) if not boxes else ocr_residual_count(out, boxes, 1.0, 1.0)
        if ocr_left > 0 and not a.prefer_art:
            # 还有能读出来的字：再来一轮更粗的掩膜
            for dilate, radius in ((26, 8), (34, 10)):
                make_mask(img_path, blocks_json, mask_png, dilate=dilate)
                m = np.asarray(Image.open(mask_png).convert("L")) if mask_png.is_file() else fallback_mask(boxes, w0, h0)
                cand = inpaint_clean(orig, m, radius=radius)
                kfc = finalize(cand)
                left, hits = ocr_residual_count(cand, boxes, 1.0, 1.0)
                attempts.append({"dilate": dilate, "radius": radius, "ocr_residual": left})
                if left == 0 or left < 0:
                    out, mask_used = cand, m
                    kf = kfc
                    kf.save(pdir / "clean_keyframe.png", format="PNG")
                    ocr_left, ocr_hits = left, hits
                    break
            mask_png.unlink(missing_ok=True)

        # 指标：用最终结果重算，不用中间轮次的
        res_count = 0
        if boxes:
            g = np.asarray(Image.fromarray(out).convert("L"))
            for b in boxes:
                if ink_residual(b, g, RESIDUAL_INK_THRESHOLD) > RESIDUAL_INK_THRESHOLD:
                    res_count += 1
        ssim_val = non_edited_ssim(orig, out, mask_used) if not a.prefer_art else 1.0
        intrusion = int(np.count_nonzero((out != orig).any(axis=2) & (mask_used == 0))) if not a.prefer_art else 0

        # 水印/淡字用「清完后还能不能读出来」验收：修补后的插画纹理会让
        # 墨迹启发式必然误判，读字才是真信号。
        faint_left = faint_text_hits(out)

        problems = []
        # 有 OCR 时以「还能不能读出字」为准；没有 OCR 才退回墨迹启发式
        effective_residual = ocr_left if ocr_left >= 0 else res_count
        if effective_residual > 0:
            problems.append(f"{effective_residual} 处仍能读出文字（OCR 残留）")
        if faint_left:
            sample = " / ".join(h["text"][:14] for h in faint_left[:3])
            problems.append(f"清字后仍有可读水印/淡字 {len(faint_left)} 处：{sample}")
        if ssim_val < SSIM_MIN:
            problems.append(f"非编辑区 SSIM {ssim_val:.3f} < {SSIM_MIN}")
        if intrusion > PROTECTED_INTRUSION_MAX:
            problems.append(f"保护带入侵 {intrusion}px")
        if not boxes_in_bounds(boxes, w0, h0):
            problems.append("文字框越界")
        if src_tag == "page" and not boxes:
            problems.append("页图带字但没有文字框，无法判定是否清干净")

        qa = {
            "qa_verdict": "PASS" if not problems else "FAIL",
            "ocr_residual_count": effective_residual,
            "non_edited_ssim": round(ssim_val, 4),
            "protected_zone_intrusion_px": intrusion,
            "checks": {
                "keyframe_size": list(kf.size),
                "source_size": [w0, h0],
                "text_box_count": len(boxes),
                "faint_text_boxes": len(faint),
                "faint_text_after_clean": len(faint_left),
                "ink_residual_boxes": res_count,
                "ocr_backend": "rapidocr" if ocr_left >= 0 else "heuristic",
                "ocr_hits": ocr_hits,
                "clean_attempts": attempts,
                "keyframe_source": src_tag,
                "mask": "s1_stroke_mask" if boxes and mask.any() else ("fallback_box_mask" if boxes else "none"),
            },
            "problems": problems,
        }
        (pdir / "qa_report.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        manifest = {
            "page_id": pid,
            "book_id": book_id,
            "source_page": page.get("source_page"),
            "scene_tag": f"{book_id}-{pid}",
            "character_ids": [c.get("id") or c.get("name") for c in (page.get("characters") or [])
                              if isinstance(c, dict)],
            "width": kf.size[0],
            "height": kf.size[1],
            "lang": lang,
            "text": (page.get("text") or "").strip(),
            "keyframe_source": src_tag,
        }
        (pdir / "page_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
        (pdir / "seedance_prompt.txt").write_text(build_prompt(page, lang, a.style_dna) + "\n", encoding="utf-8")

        rows.append({"dir": safe, "page_id": pid, "size": list(kf.size),
                     "verdict": qa["qa_verdict"], "residual": (ocr_left if ocr_left >= 0 else res_count),
                     "ssim": qa["non_edited_ssim"], "intrusion": intrusion})

    summary = {"book_id": book_id, "lang": lang, "pages": len(rows),
               "pass": sum(1 for r in rows if r["verdict"] == "PASS"),
               "fail": sum(1 for r in rows if r["verdict"] != "PASS"), "rows": rows}
    (out_dir / "prepare_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")

    print(f"[prepare] {book_id}: {summary['pages']} 页, PASS {summary['pass']}, FAIL {summary['fail']}")
    for r in rows:
        print(f"  {r['dir']:<5} {r['size'][0]}x{r['size'][1]}  residual={r['residual']}  "
              f"ssim={r['ssim']:.4f}  intrusion={r['intrusion']}  {r['verdict']}")
    return 0 if summary["fail"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
