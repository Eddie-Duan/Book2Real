"""
common_io.py — IO + metric primitives for picturebook-scene-director.

Hard dependencies: numpy, Pillow.
Optional accelerators: OpenCV (mp4 decode in qa_seams), ffmpeg on PATH (same).
No scikit-image, no imagehash, no torch.

Every metric here is a deterministic CPU proxy for the director's judgement,
not ground truth. See references/metrics.md for what each proxy can and cannot
prove.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

# --------------------------------------------------------------------- IO


def imread(path) -> np.ndarray:
    """Read an image as RGB uint8 HxWx3. Unicode-safe (Pillow)."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        return np.asarray(im.convert("RGB"), dtype=np.uint8)


def imwrite(path, rgb: np.ndarray) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8), mode="RGB").save(p)


def to_gray(rgb: np.ndarray) -> np.ndarray:
    a = np.asarray(rgb, dtype=np.float32)
    return (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]) / 255.0


def resize_rgb(rgb: np.ndarray, size) -> np.ndarray:
    return np.asarray(Image.fromarray(rgb).resize(size, Image.BILINEAR), dtype=np.uint8)


def resize_gray(gray: np.ndarray, size) -> np.ndarray:
    g8 = np.clip(gray * 255.0, 0, 255).astype(np.uint8)
    return np.asarray(Image.fromarray(g8).resize(size, Image.BILINEAR), dtype=np.float32) / 255.0


def blur_rgb(rgb: np.ndarray, radius: float) -> np.ndarray:
    return np.asarray(Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(radius)), dtype=np.uint8)


def mean_luma(rgb: np.ndarray) -> float:
    return float(to_gray(rgb).mean())


# ------------------------------------------------------------------ SSIM

_DOWNSCALE_MAX = 640


def _box_mean(x: np.ndarray, win: int) -> np.ndarray:
    """Uniform (box) filter through an integral image. O(1) per pixel."""
    r = win // 2
    p = np.pad(x, r, mode="reflect")
    ii = p.cumsum(0).cumsum(1)
    ii = np.pad(ii, ((1, 0), (1, 0)))
    h, w = x.shape
    s = (
        ii[win : win + h, win : win + w]
        - ii[0:h, win : win + w]
        - ii[win : win + h, 0:w]
        + ii[0:h, 0:w]
    )
    return s / float(win * win)


def ssim_gray(a: np.ndarray, b: np.ndarray, win: int = 7) -> float:
    if a.shape != b.shape:
        b = resize_gray(b, (a.shape[1], a.shape[0]))
    h, w = a.shape
    m = max(h, w)
    if m > _DOWNSCALE_MAX:
        s = _DOWNSCALE_MAX / float(m)
        size = (max(8, int(round(w * s))), max(8, int(round(h * s))))
        a = resize_gray(a, size)
        b = resize_gray(b, size)
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    mu_a, mu_b = _box_mean(a, win), _box_mean(b, win)
    saa = _box_mean(a * a, win) - mu_a * mu_a
    sbb = _box_mean(b * b, win) - mu_b * mu_b
    sab = _box_mean(a * b, win) - mu_a * mu_b
    num = (2.0 * mu_a * mu_b + c1) * (2.0 * sab + c2)
    den = (mu_a ** 2 + mu_b ** 2 + c1) * (saa + sbb + c2)
    return float(np.mean(num / den))


# ------------------------------------------------------------------ pHash


def _dct_matrix(n: int = 32) -> np.ndarray:
    k = np.arange(n)[:, None]
    x = np.arange(n)[None, :]
    return np.cos(np.pi * (2 * x + 1) * k / (2.0 * n))


_DCT32 = _dct_matrix(32)


def phash(gray: np.ndarray, hash_size: int = 8, highfreq_factor: int = 4) -> int:
    n = hash_size * highfreq_factor
    g = resize_gray(gray, (n, n))
    d = _DCT32[:n, :n] @ g @ _DCT32[:n, :n].T
    low = d[:hash_size, :hash_size]
    med = float(np.median(low.flatten()[1:]))
    bits = (low > med).flatten()
    v = 0
    for bit in bits:
        v = (v << 1) | int(bool(bit))
    return v


def hamming(a: int, b: int) -> int:
    return bin(int(a) ^ int(b)).count("1")


# --------------------------------------------------------- colour distance


def _hsv_hist(rgb: np.ndarray, bins: int = 32) -> np.ndarray:
    a = np.asarray(rgb, dtype=np.float32) / 255.0
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = a.max(-1)
    mn = a.min(-1)
    df = mx - mn + 1e-8
    h = np.zeros_like(mx)
    mask = mx == r
    h[mask] = ((g - b) / df)[mask] % 6.0
    mask = mx == g
    h[mask] = ((b - r) / df)[mask] + 2.0
    mask = mx == b
    h[mask] = ((r - g) / df)[mask] + 4.0
    h = h / 6.0
    s = df / (mx + 1e-8)
    hi = np.clip((h * bins).astype(np.int32), 0, bins - 1)
    si = np.clip((s * bins).astype(np.int32), 0, bins - 1)
    hist = np.zeros((bins, bins), dtype=np.float64)
    np.add.at(hist, (hi.ravel(), si.ravel()), 1.0)
    return hist / (hist.sum() + 1e-8)


def bhattacharyya(rgb_a: np.ndarray, rgb_b: np.ndarray, bins: int = 32) -> float:
    """sqrt(1 - sum(sqrt(p*q))) — same shape as cv2.HISTCMP_BHATTACHARYYA."""
    p = _hsv_hist(rgb_a, bins)
    q = _hsv_hist(rgb_b, bins)
    bc = float(np.sum(np.sqrt(p * q)))
    return float(np.sqrt(max(0.0, 1.0 - bc)))


# ------------------------------------------------------- feature distance


def feature_vec(rgb: np.ndarray) -> np.ndarray:
    """64x64 HSV histogram (60%) + gradient-orientation histogram (40%)."""
    small = resize_rgb(rgb, (64, 64))
    hsv = _hsv_hist(small, bins=16).flatten()
    hsv = hsv / (np.linalg.norm(hsv) + 1e-8)
    g = to_gray(small)
    gy, gx = np.gradient(g)
    mag = np.hypot(gx, gy)
    ang = np.degrees(np.arctan2(gy, gx)) % 360.0
    bins = np.clip((ang / 22.5).astype(np.int32), 0, 15)
    hog = np.bincount(bins.ravel(), weights=mag.ravel(), minlength=16).astype(np.float64)
    hog = hog / (np.linalg.norm(hog) + 1e-8)
    vec = np.concatenate([hsv * 0.6, hog * 0.4])
    return vec / (np.linalg.norm(vec) + 1e-8)


def feature_distance(rgb_a: np.ndarray, rgb_b: np.ndarray) -> float:
    d = 1.0 - float(np.dot(feature_vec(rgb_a), feature_vec(rgb_b)))
    return float(np.clip(d, 0.0, 1.0))


# ------------------------------------------------------------- edge masks


def edge_mask(gray: np.ndarray, percentile: float = 85.0) -> np.ndarray:
    gy, gx = np.gradient(gray)
    mag = np.hypot(gx, gy)
    thr = max(float(np.percentile(mag, percentile)), 1e-4)
    return mag > thr


def _dilate(mask: np.ndarray) -> np.ndarray:
    out = mask.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        out |= np.roll(mask, shift=(dy, dx), axis=(0, 1))
    return out


def edge_bbox(gray: np.ndarray) -> tuple:
    """Return (cx, cy, area_fraction) of the salient edge cloud."""
    m = edge_mask(gray)
    ys, xs = np.nonzero(m)
    if xs.size < 20:
        return 0.5, 0.5, 0.25
    x1, x2 = np.percentile(xs, [10, 90])
    y1, y2 = np.percentile(ys, [10, 90])
    h, w = gray.shape
    cx = float((x1 + x2) * 0.5 / w)
    cy = float((y1 + y2) * 0.5 / h)
    area = max(float((x2 - x1) * (y2 - y1)) / float(w * h), 1e-4)
    return cx, cy, area


def shape_iou(gray_a: np.ndarray, gray_b: np.ndarray) -> float:
    """IoU of dilated edge masks — a silhouette-coincidence proxy."""
    b = resize_gray(gray_b, (gray_a.shape[1], gray_a.shape[0]))
    ma = _dilate(edge_mask(gray_a))
    mb = _dilate(edge_mask(b))
    inter = float(np.logical_and(ma, mb).sum())
    union = float(np.logical_or(ma, mb).sum())
    return inter / (union + 1e-8)


# ------------------------------------------------------------------ flow


def phase_shift(a: np.ndarray, b: np.ndarray) -> tuple:
    """Global translation (dx, dy) that maps content of a onto b, in pixels."""
    fa = np.fft.fft2(a - a.mean())
    fb = np.fft.fft2(b - b.mean())
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    corr = np.fft.ifft2(r).real
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = a.shape
    if dy > h // 2:
        dy -= h
    if dx > w // 2:
        dx -= w
    return float(dx), float(dy)


def flow_stats(gray_a: np.ndarray, gray_b: np.ndarray, work: int = 160, grid=(4, 3)) -> dict:
    """Block phase-correlation flow.

    Returns mean displacement in ORIGINAL pixel units plus the fraction of
    blocks whose forward/backward vectors cancel (occlusion / new-content
    inconsistency lowers it).
    """
    h, w = gray_a.shape
    wh = max(48, int(round(work * h / float(w))))
    a = resize_gray(gray_a, (work, wh))
    b = resize_gray(gray_b, (work, wh))
    gw, gh = grid
    bw, bh = max(8, work // gw), max(8, wh // gh)
    fw, bwv = [], []
    for iy in range(gh):
        for ix in range(gw):
            y0, x0 = iy * bh, ix * bw
            pa = a[y0 : y0 + bh, x0 : x0 + bw]
            pb = b[y0 : y0 + bh, x0 : x0 + bw]
            if pa.std() < 0.015 or pb.std() < 0.015:
                continue
            dx, dy = phase_shift(pa, pb)
            bx, by = phase_shift(pb, pa)
            fw.append((dx, dy))
            bwv.append((bx, by))
    if not fw:
        return {"dx": 0.0, "dy": 0.0, "consistency": 0.0, "valid_blocks": 0}
    f = np.asarray(fw, dtype=np.float64)
    r = np.asarray(bwv, dtype=np.float64)
    err = np.linalg.norm(f + r, axis=1)
    consistency = float(np.mean(err <= 2.0))
    scale = w / float(work)
    return {
        "dx": float(f[:, 0].mean() * scale),
        "dy": float(f[:, 1].mean() * scale),
        "consistency": consistency,
        "valid_blocks": int(len(fw)),
    }


def flow_consistency(gray_a: np.ndarray, gray_b: np.ndarray) -> float:
    return float(flow_stats(gray_a, gray_b)["consistency"])


def flow_vector(gray_a: np.ndarray, gray_b: np.ndarray) -> np.ndarray:
    s = flow_stats(gray_a, gray_b)
    return np.array([s["dx"], s["dy"]], dtype=np.float64)


# --------------------------------------------------------------- video IO


def _ffmpeg_exe() -> str | None:
    return shutil.which("ffmpeg")


def _ffmpeg_boundary_frames(clip: Path) -> list:
    exe = _ffmpeg_exe()
    if exe is None:
        raise RuntimeError(
            f"无法读取视频 {clip}: 既没有 OpenCV 也没有 PATH 上的 ffmpeg。"
            " 请安装 opencv-python-headless，或先用 ffmpeg 抽帧再用 --clips-dir 指向帧目录。"
        )
    tmp = Path(tempfile.mkdtemp(prefix="pbdir_"))
    first = tmp / "first.png"
    last = tmp / "last.png"
    subprocess.run(
        [exe, "-y", "-loglevel", "error", "-i", str(clip), "-frames:v", "1", str(first)],
        check=True,
    )
    subprocess.run(
        [exe, "-y", "-loglevel", "error", "-sseof", "-0.2", "-i", str(clip),
         "-frames:v", "1", str(last)],
        check=True,
    )
    frames = [imread(first), imread(last)]
    shutil.rmtree(tmp, ignore_errors=True)
    return frames


def load_clip_frames(clip_path) -> list:
    """Return boundary frames of one clip.

    Accepts:
      * a directory of PNG/JPG frames (sorted) -> all frames;
      * an image file -> [image, image];
      * a video file -> [first, (second), (penultimate), last] via cv2 or ffmpeg.
    """
    p = Path(clip_path)
    if p.is_dir():
        pics = sorted(
            q for q in p.iterdir() if q.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        )
        if not pics:
            raise RuntimeError(f"帧目录为空: {p}")
        return [imread(q) for q in pics]
    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        img = imread(p)
        return [img, img]

    try:
        import cv2  # noqa: F401  (optional)

        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频: {p}")
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total < 2:
            cap.release()
            raise RuntimeError(f"视频帧数不足: {p}")
        idx = [0, 1, max(0, total - 2), total - 1]
        out = []
        for i in idx:
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, frame = cap.read()
            if ok and frame is not None:
                out.append(np.asarray(frame[:, :, ::-1], dtype=np.uint8))
        cap.release()
        if len(out) < 2:
            raise RuntimeError(f"视频解码失败: {p}")
        return out
    except ImportError:
        return _ffmpeg_boundary_frames(p)
