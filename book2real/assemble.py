"""Film assembly: clips -> one playable film with title, subtitles and audio."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np

from .config import FilmConfig
from .util import ensure_dir, ffmpeg_path, imread, imwrite


def _font(size: int):
    from PIL import ImageFont

    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc", "arial.ttf"):
        p = Path("C:/Windows/Fonts") / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size, index=0)
            except Exception:
                continue
    return ImageFont.load_default()


def _center_text(draw, xy, text, font, fill, shadow=(0, 0, 0, 170)):
    x, y = xy
    draw.text((x + 2, y + 2), text, font=font, fill=shadow, anchor="mm")
    draw.text((x, y), text, font=font, fill=fill, anchor="mm")


def make_title_clip(title: str, subtitle: str, bg_path: Path | None, out: Path,
                    cfg: FilmConfig, seconds: float = 3.0) -> Path:
    """Generate a title card clip, using the first page as a blurred backdrop."""
    from PIL import Image, ImageDraw, ImageFilter

    W, H = cfg.width, cfg.height
    if bg_path and Path(bg_path).exists():
        base = Image.open(bg_path).convert("RGB")
        s = max(W / base.width, H / base.height)
        base = base.resize((int(base.width * s) + 1, int(base.height * s) + 1), Image.LANCZOS)
        x0 = (base.width - W) // 2
        y0 = (base.height - H) // 2
        base = base.crop((x0, y0, x0 + W, y0 + H)).filter(ImageFilter.GaussianBlur(14))
        img = Image.blend(base, Image.new("RGB", (W, H), (18, 16, 14)), 0.62)
    else:
        img = Image.new("RGB", (W, H), (18, 16, 14))

    top = _font(int(H * 0.085))
    small = _font(int(H * 0.032))
    draw = ImageDraw.Draw(img, "RGBA")
    _center_text(draw, (W // 2, int(H * 0.44)), title, top, (247, 242, 232, 255))
    _center_text(draw, (W // 2, int(H * 0.57)), subtitle, small, (214, 203, 186, 235))
    draw.line([(W * 0.40, H * 0.51), (W * 0.60, H * 0.51)], fill=(200, 170, 120, 120), width=2)

    arr = np.asarray(img, dtype=np.uint8).copy()
    n = max(1, int(round(seconds * cfg.fps)))
    out = Path(out)
    ensure_dir(out.parent)

    from .encode import encode_frames

    def frames():
        for i in range(n):
            k = min(1.0, i / max(1, int(0.6 * cfg.fps)),
                     (n - 1 - i) / max(1, int(0.6 * cfg.fps)))
            f = arr.astype(np.float32) * k
            yield np.clip(f, 0, 255).astype(np.uint8)

    return encode_frames(frames(), out, cfg.fps, (W, H))


def make_end_clip(text: str, out: Path, cfg: FilmConfig, seconds: float = 2.0) -> Path:
    from PIL import Image, ImageDraw

    W, H = cfg.width, cfg.height
    img = Image.new("RGB", (W, H), (16, 15, 14))
    draw = ImageDraw.Draw(img, "RGBA")
    _center_text(draw, (W // 2, H // 2 - 10), text, _font(int(H * 0.036)), (196, 186, 170, 255))
    arr = np.asarray(img, dtype=np.uint8).copy()
    n = max(1, int(round(seconds * cfg.fps)))
    out = Path(out)
    ensure_dir(out.parent)

    from .encode import encode_frames

    def frames():
        for i in range(n):
            k = min(1.0, i / 8.0, (n - 1 - i) / 8.0)
            yield np.clip(arr.astype(np.float32) * k, 0, 255).astype(np.uint8)

    return encode_frames(frames(), out, cfg.fps, (W, H))


def write_ass(cues: list[tuple], path: Path, cfg: FilmConfig) -> Path:
    """cues: [(start_s, end_s, text)] -> ASS subtitle file."""
    def ts(t: float) -> str:
        t = max(0.0, t)
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    fs = int(cfg.height * 0.047)
    body = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {cfg.width}",
        f"PlayResY: {cfg.height}", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Microsoft YaHei,{fs},&H00FFFFFF,&H000000FF,&H00201812,&H8C000000,"
        f"-1,0,0,0,100,100,0.4,0,1,2.6,1.2,2,90,90,{int(cfg.height * 0.055)},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in cues:
        if not text:
            continue
        safe = text.replace("\n", " ").replace("{", "(").replace("}", ")")
        body.append(f"Dialogue: 0,{ts(start)},{ts(end)},Default,,0,0,0,,{safe}")
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text("\n".join(body) + "\n", encoding="utf-8-sig")
    return p


def concat(clips: list[Path], out: Path, work_dir: Path) -> Path:
    lst = Path(work_dir) / "concat.txt"
    lst.write_text("".join(f"file '{Path(c).as_posix()}'\n" for c in clips), encoding="utf-8")
    ensure_dir(Path(out).parent)
    subprocess.run([
        ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out),
    ], capture_output=True, check=True)
    return out


def finish(raw: Path, out: Path, cfg: FilmConfig, *,
           ass: Path | None = None, audio: Path | None = None) -> Path:
    """Burn subtitles, attach audio, and encode the delivery file.

    ffmpeg's filter parser treats ':' as an option separator, so the .ass file
    is referenced by bare name with cwd set to its folder.
    """
    ensure_dir(Path(out).parent)
    cwd = Path(ass).parent if ass else Path(raw).parent
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-i", str(Path(raw).resolve())]
    if audio and Path(audio).exists():
        cmd += ["-i", str(Path(audio).resolve())]
    if ass and Path(ass).exists():
        cmd += ["-vf", f"ass={Path(ass).name}"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart"]
    if audio and Path(audio).exists():
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += [str(Path(out).resolve())]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(cwd))
    if p.returncode != 0:
        raise RuntimeError("finish failed:\n" + "\n".join(p.stderr.strip().splitlines()[-10:]))
    return Path(out)
