"""Render the interactive book into a 16:9 'read-along' film.

Left: large characters with pinyin. Right: the cleaned illustration with a
slow 2.5D camera move. One shot per reader page, no generative redraw, so the
art stays exactly the book's art.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import imgops as I
from .encode import encode_frames
from .util import ensure_dir, ffmpeg_path, ffprobe, imread, write_json

W, H = 1280, 720
FPS = 24
ROOT = Path(__file__).resolve().parents[1]
FONT = Path("C:/Windows/Fonts/msyh.ttc")
FONT_B = Path("C:/Windows/Fonts/msyhbd.ttc")
KAI = Path("C:/Windows/Fonts/simkai.ttf")


def _font(size: int, bold: bool = False, kai: bool = False):
    for p in ([KAI] if kai else []) + ([FONT_B] if bold else [FONT]):
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size, index=0)
            except Exception:
                continue
    return ImageFont.load_default()


def _paper_bg(theme: dict) -> np.ndarray:
    paper = theme.get("paper", "#f6efe2")
    base = Image.new("RGB", (W, H), paper)
    d = ImageDraw.Draw(base, "RGBA")
    # warm light from the right
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((W * 0.48, -H * 0.3, W * 1.25, H * 1.1), fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    base = Image.composite(Image.new("RGB", (W, H), "#fff8ea"), base, glow)
    # grain
    rng = np.random.default_rng(11)
    arr = np.asarray(base).astype(np.float32)
    noise = rng.normal(0, 2.2, (H, W, 1)).astype(np.float32)
    arr = np.clip(arr + noise, 0, 255)
    # vignette + inner border
    yy, xx = np.mgrid[0:H, 0:W]
    cx, cy = W / 2, H / 2
    v = 1.0 - 0.18 * (((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2) / 2
    arr *= v[..., None]
    base = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(base, "RGBA")
    d.rectangle((18, 18, W - 18, H - 18), outline=(120, 95, 70, 38), width=1)
    return np.asarray(base)


def _fit(im: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = im.shape[:2]
    s = min(w / iw, h / ih)
    return I.resize(im, max(1, int(round(iw * s))), max(1, int(round(ih * s))))


def _rounded(im: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1),
                                           radius=radius, fill=255)
    im.putalpha(mask)
    return im


def _layout(page: dict, panel_w: int, panel_h: int) -> tuple[list, int, int]:
    """Wrap pinyin tokens by word groups so a word is never split across lines."""
    tokens = page.get("pinyin") or []
    groups = page.get("segments") or [t.get("ch", "") for t in tokens]
    lines: list = []
    for size in range(50, 25, -2):
        cf = _font(size)
        line_h = int(size * 1.06 + max(13, size * 0.36) * 1.25 + 18)
        lines, cur, cur_w, ti = [], [], 0.0, 0
        for seg in groups:
            toks = tokens[ti:ti + len(seg)]
            ti += len(seg)
            if not toks:
                continue
            w = sum(cf.getlength(t.get("ch", "")) + 2 for t in toks)
            if cur and cur_w + w > panel_w:
                lines.append(cur)
                cur, cur_w = [], 0.0
            cur.extend(toks)
            cur_w += w
        if cur:
            lines.append(cur)
        if len(lines) * line_h <= panel_h:
            return lines, size, line_h
    return lines, 28, 52


def _text_panel(page: dict, bg: np.ndarray, theme: dict) -> np.ndarray:
    img = Image.fromarray(bg.copy())
    d = ImageDraw.Draw(img, "RGBA")
    ink = theme.get("ink", "#2f2a24")
    soft = theme.get("soft", "#8c7a63")
    accent = theme.get("accent", "#c65a3f")

    x0, y0 = 76, 96
    kicker = f"第 {page.get('source_page', page.get('page', 1))} 页"
    if page.get("parts", 1) > 1:
        kicker += f" · {page.get('part')}/{page.get('parts')}"
    d.text((x0, y0), kicker, font=_font(14), fill=soft)
    d.line((x0, y0 + 34, x0 + 46, y0 + 34), fill=accent, width=3)

    if page.get("kind") == "cover":
        title = page.get("_book_title", "")
        d.text((x0, y0 + 76), title, font=_font(56, kai=True), fill=ink)
        d.text((x0, y0 + 158), page.get("_book_subtitle", ""), font=_font(20), fill=soft)
        d.text((x0, y0 + 196), page.get("_book_author", ""), font=_font(15), fill=soft)
        d.text((x0, H - 128), "点一点字，听读音；点一点画，看讲解。", font=_font(15), fill=soft)
        return np.asarray(img)

    lines, size, line_h = _layout(page, 520, 500)
    cf, pyf = _font(size), _font(max(13, int(size * 0.36)))
    ty = 190
    for line in lines:
        x = x0
        for t in line:
            ch = t.get("ch", "")
            py = t.get("py", "")
            cw = cf.getlength(ch)
            if py:
                d.text((x + cw / 2, ty), py, font=pyf, fill=soft, anchor="ma")
            d.text((x, ty + int(size * 0.36) * 1.25 + 4), ch, font=cf, fill=ink)
            x += cw + 2
        ty += line_h
    # scene caption at the bottom, kept quiet
    scene = (page.get("scene") or "").strip()
    if scene:
        d.line((x0, H - 76, x0 + 520, H - 76), fill=(120, 95, 70, 42), width=1)
        cap = scene if len(scene) <= 44 else scene[:44] + "…"
        d.text((x0, H - 62), cap, font=_font(14), fill=soft)
    return np.asarray(img)


def _art_rect(im: np.ndarray) -> tuple[np.ndarray, int, int, int, int]:
    area_w, area_h = 590, 600
    aw, ah = im.shape[1], im.shape[0]
    s = min(area_w / aw, area_h / ah)
    aw, ah = max(1, int(aw * s)), max(1, int(ah * s))
    small = I.resize(im, aw, ah)
    ax, ay = 640 + (area_w - aw) // 2, 60 + (area_h - ah) // 2
    return small, ax, ay, aw, ah


def _shadow(art: Image.Image) -> Image.Image:
    sh = Image.new("RGBA", (art.width + 60, art.height + 60), (0, 0, 0, 0))
    d = ImageDraw.Draw(sh)
    d.rounded_rectangle((30, 34, 30 + art.width, 34 + art.height), radius=22, fill=(60, 40, 24, 70))
    return sh.filter(ImageFilter.GaussianBlur(18))


def _composite(panel: np.ndarray, art: Image.Image, shadow: Image.Image,
               ax: int, ay: int, scale: float, dx: float, dy: float,
               t: float) -> np.ndarray:
    frame = Image.fromarray(panel.copy()).convert("RGB")
    frame.paste(shadow, (ax - 30 + int(dx * 10), ay - 30 + int(dy * 10)), shadow)
    w, h = art.size
    warped = art.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
    x = ax + (w - warped.width) // 2 + int(dx * 18)
    y = ay + (h - warped.height) // 2 + int(dy * 14)
    frame.paste(warped, (x, y), warped)
    arr = np.asarray(frame).astype(np.float32)
    centre = -0.2 + 1.45 * (0.15 + 0.7 * t)
    band = np.exp(-((np.arange(W) / W - centre) ** 2) / (2 * 0.13 ** 2)) * 10.0
    arr = np.clip(arr + band[None, :, None], 0, 255)
    return arr.astype(np.uint8)


def _duration(page: dict) -> float:
    if page.get("_audio_dur"):
        return max(3.2, min(11.0, page["_audio_dur"] + 0.9))
    return max(3.4, min(9.0, 2.2 + len(page.get("text", "")) * 0.18))


def _frames(book: dict, on_progress=None):
    theme = book.get("theme") or {}
    bg = _paper_bg(theme)
    pages = book["pages"]
    total = len(pages)
    title = book.get("title", "")

    # cover / title card
    cover = next((p for p in pages if p.get("kind") == "cover"), None)
    if cover:
        cover = dict(cover, _book_title=title, _book_subtitle=book.get("subtitle", ""),
                     _book_author=book.get("author", ""))
        panel = _text_panel(cover, bg, theme)
        art = _fit(imread(ROOT / "books" / book["book_id"] / "reader" / cover["art"]), 590, 600)
        art_img = _rounded(Image.fromarray(art), 18)
        shadow = _shadow(art_img)
        ax, ay = 640 + (590 - art_img.width) // 2, 60 + (600 - art_img.height) // 2
        n = FPS * 3
        for i in range(n):
            t = i / (n - 1)
            f = _composite(panel if i else bg, art_img, shadow, ax, ay, 1.0 + 0.02 * t, 0, 0, t)
            k = min(1.0, i / 12.0)
            yield (bg.astype(np.float32) * (1 - k) + f.astype(np.float32) * k).astype(np.uint8)
        pages = [p for p in pages if p.get("kind") != "cover"]
        total = len(pages)

    for idx, page in enumerate(pages):
        if on_progress:
            on_progress(idx, max(1, total), f"绘制第 {idx + 1}/{total} 页")
        panel = _text_panel(page, bg, theme)
        art_path = ROOT / "books" / book["book_id"] / "reader" / page["art"]
        art = _fit(imread(art_path), 590, 600)
        art_img = _rounded(Image.fromarray(art), 18)
        shadow = _shadow(art_img)
        ax, ay = 640 + (590 - art_img.width) // 2, 60 + (600 - art_img.height) // 2
        dur = _duration(page)
        n = max(1, int(round(dur * FPS)))
        for i in range(n):
            t = i / (n - 1)
            scale = 1.02 + 0.055 * t
            dx = 0.006 * np.sin(t * np.pi * 2) * (1 if idx % 2 else -1)
            dy = -0.004 * t
            f = _composite(panel, art_img, shadow, ax, ay, scale, dx, dy, t)
            k = min(1.0, i / 7.0, (n - 1 - i) / 7.0)
            yield (bg.astype(np.float32) * (1 - k) + f.astype(np.float32) * k).astype(np.uint8)

    # closing card
    end = bg.copy()
    im = Image.fromarray(end)
    d = ImageDraw.Draw(im, "RGBA")
    d.text((W // 2, H // 2 - 16), "读完了。", font=_font(40, kai=True),
           fill=theme.get("ink", "#2f2a24"), anchor="mm")
    d.text((W // 2, H // 2 + 34), "再读一遍，或者点开一个字看看。", font=_font(16), fill=theme.get("soft", "#8c7a63"), anchor="mm")
    arr = np.asarray(im).astype(np.float32)
    for i in range(FPS * 2):
        k = min(1.0, i / 12.0)
        yield (bg.astype(np.float32) * (1 - k) + arr * k).astype(np.uint8)
    if on_progress:
        on_progress(total, total, "编码影片")


def _narrate(book: dict, on_progress=None) -> None:
    """Pre-synthesise one mp3 per story page (cached by text)."""
    from . import tts

    pages = [p for p in book["pages"] if p.get("kind") != "cover"]
    for i, page in enumerate(pages):
        if on_progress:
            on_progress(i, max(1, len(pages)), f"配音第 {i + 1}/{len(pages)} 页")
        try:
            path, dur = tts.synth(page.get("text", ""))
            page["_audio"] = str(path)
            page["_audio_dur"] = dur
        except Exception:
            page["_audio"] = ""
            page["_audio_dur"] = 0.0


def _audio_track(book: dict, run_dir: Path) -> Path | None:
    """Pad each page's narration to its shot length and concatenate."""
    parts = []
    entries = [("silence", 3.0)]
    for p in book["pages"]:
        if p.get("kind") == "cover":
            continue
        entries.append((p.get("_audio") or "silence", _duration(p)))
    if not any(src != "silence" for src, _ in entries):
        return None
    entries.append(("silence", 2.0))
    work = ensure_dir(run_dir / "audio")
    for i, (src, dur) in enumerate(entries):
        wav = work / f"{i:03d}.wav"
        if src == "silence":
            cmd = [ffmpeg_path(), "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                   "-t", f"{dur:.3f}", "-c:a", "pcm_s16le", str(wav)]
        else:
            cmd = [ffmpeg_path(), "-y", "-i", src, "-af", "apad", "-t", f"{dur:.3f}",
                   "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)]
        subprocess.run(cmd, check=True, capture_output=True)
        parts.append(wav)
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    track = work / "narration.wav"
    subprocess.run([ffmpeg_path(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(track)], check=True, capture_output=True)
    return track


def render(book_dir: str | Path, on_progress=None) -> dict:
    book_dir = Path(book_dir).resolve()
    book = json.loads((book_dir / "reader" / "book.json").read_text(encoding="utf-8"))
    run_dir = ensure_dir(ROOT / "runs" / book["book_id"] / "reader_film" / time.strftime("%Y%m%d-%H%M%S"))
    out = run_dir / "film.mp4"
    silent = run_dir / "film_silent.mp4"
    t0 = time.perf_counter()
    _narrate(book, on_progress)
    if on_progress:
        on_progress(0, 1, "排版与绘制")
    encode_frames(_frames(book, on_progress), silent, FPS, (W, H))
    track = None
    try:
        track = _audio_track(book, run_dir)
    except Exception:
        track = None
    if track:
        subprocess.run([ffmpeg_path(), "-y", "-i", str(silent), "-i", str(track),
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                        "-shortest", str(out)], check=True, capture_output=True)
        silent.unlink(missing_ok=True)
    else:
        silent.replace(out)
    dur = 0.0
    try:
        info = ffprobe(out)
        dur = float(info.get("format", {}).get("duration", 0) or 0)
    except Exception:
        pass
    meta = {"book_id": book["book_id"], "title": book.get("title", ""),
            "path": out.relative_to(ROOT / "runs").as_posix(),
            "duration": round(dur, 2), "width": W, "height": H, "fps": FPS,
            "pages": len(book["pages"]), "narrated": bool(track),
            "wall_s": round(time.perf_counter() - t0, 1)}
    write_json(run_dir / "manifest.json", meta)
    return meta
