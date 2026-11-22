#!/usr/bin/env python3
"""
公共基础设施：配置加载、日志脱敏、状态机持久化、GPU/CPU 视觉特征与 OCR 轻量化兜底引擎。
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import yaml
from PIL import Image
from skimage.metrics import structural_similarity as ssim_metric

# 尝试加载重依赖，失败时启用轻量确定性兜底并标记 degraded_mode
HAS_TORCH = False
HAS_TRANSFORMERS = False
HAS_EASYOCR = False

try:
    import torch
    HAS_TORCH = True
except ImportError:
    pass

try:
    from transformers import AutoImageProcessor, AutoModel
    HAS_TRANSFORMERS = True
except ImportError:
    pass

try:
    import easyocr
    HAS_EASYOCR = True
except ImportError:
    pass


# ------------------------------------------------------------ 外部工具定位兜底
# winget 的便携包（如 Gyan.FFmpeg）会落在 Packages 目录下而不进 PATH，
# 此时直接调用 ffmpeg/ffprobe 会 FileNotFoundError。这里只在 PATH 里找不到时才介入：
# 到几个公认安装位置探测，命中后把所在目录前置到本进程 PATH，供后续 subprocess 使用。


def _external_tool_bins() -> List[Path]:
    bins: List[Path] = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            bins.append(Path(local) / "Microsoft" / "WinGet" / "Links")
            pkgroot = Path(local) / "Microsoft" / "WinGet" / "Packages"
            if pkgroot.is_dir():
                for pkg in sorted(pkgroot.glob("Gyan.FFmpeg*")):
                    bins.extend(sorted(pkg.rglob("bin")))
        bins.extend(
            [
                Path(r"C:\ffmpeg\bin"),
                Path(r"C:\Program Files\ffmpeg\bin"),
                Path(r"C:\ProgramData\chocolatey\bin"),
            ]
        )
    else:
        bins.extend([Path("/usr/local/bin"), Path("/opt/homebrew/bin"), Path("/usr/bin")])
    return [b for b in bins if b.is_dir()]


def ensure_external_tools_on_path() -> List[str]:
    """PATH 中缺 ffmpeg/ffprobe 时补上它们所在目录，返回新加入的目录列表。"""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return []
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    added: List[str] = []
    for candidate in _external_tool_bins():
        has_ffmpeg = shutil.which("ffmpeg", path=str(candidate)) is not None
        has_ffprobe = shutil.which("ffprobe", path=str(candidate)) is not None
        if has_ffmpeg and has_ffprobe and str(candidate) not in parts:
            parts.insert(0, str(candidate))
            added.append(str(candidate))
            break
    if added:
        os.environ["PATH"] = os.pathsep.join(parts)
    return added


_EXTERNAL_TOOL_DIRS_ADDED = ensure_external_tools_on_path()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_secret(text: str) -> str:
    """对日志中的 API Key 和长 Base64 载荷进行脱敏。"""
    if not text:
        return text
    api_key = os.environ.get("ARK_API_KEY", "")
    if api_key and len(api_key) > 6:
        masked = f"{api_key[:3]}***{api_key[-4:]}"
        text = text.replace(api_key, masked)
    text = re.sub(r"Bearer\s+[A-Za-z0-9\-_\.]+", "Bearer ***REDACTED***", text)
    text = re.sub(r"data:image/[a-zA-Z]+;base64,[A-Za-z0-9+/=]{40,}", "data:image/png;base64,<REDACTED_B64>", text)
    return text


def append_run_log(out_dir: Path, event: str, payload: Dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run_log.jsonl"
    record = {
        "ts": utc_now_iso(),
        "event": event,
        "data": json.loads(sanitize_secret(json.dumps(payload, ensure_ascii=False))),
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    if config_path and config_path.is_file():
        return yaml.safe_load(config_path.read_text(encoding="utf-8"))
    default_cfg = Path("config.yaml")
    if default_cfg.is_file():
        return yaml.safe_load(default_cfg.read_text(encoding="utf-8"))
    example_cfg = Path(__file__).resolve().parent.parent / "config.example.yaml"
    return yaml.safe_load(example_cfg.read_text(encoding="utf-8"))


def load_env_file(env_path: Path = Path(".env")) -> None:
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def encode_image_to_data_url(image_path: Path, max_bytes: int = 10 * 1024 * 1024) -> str:
    raw = image_path.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError(f"Keyframe {image_path} exceeds Ark max payload size ({len(raw)} > {max_bytes} bytes)")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{b64}"


def imread_bgr(image_path: Path) -> Optional[np.ndarray]:
    """读图（BGR）。替代 cv2.imread：后者在 Windows 非 ASCII 路径下直接返回 None。

    走 np.fromfile + cv2.imdecode，绕开 OpenCV 对路径编码的限制。
    """
    try:
        raw = Path(image_path).read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)


def imwrite_bgr(image_path: Path, image: np.ndarray, ext: Optional[str] = None) -> bool:
    """写图（BGR）。替代 cv2.imwrite：后者在 Windows 非 ASCII 路径下静默失败返回 False。"""
    suffix = (ext or Path(image_path).suffix or ".png").lower()
    if not suffix.startswith("."):
        suffix = "." + suffix
    try:
        ok, buf = cv2.imencode(suffix, image)
    except cv2.error:
        return False
    if not ok:
        return False
    try:
        Path(image_path).write_bytes(buf.tobytes())
    except OSError:
        return False
    return True


def check_environment_capabilities() -> Dict[str, Any]:
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    gpu_available = bool(HAS_TORCH and torch.cuda.is_available())
    gpu_name = torch.cuda.get_device_name(0) if gpu_available else None
    return {
        "ffmpeg_available": bool(ffmpeg_bin),
        "ffprobe_available": bool(ffprobe_bin),
        "ffmpeg_path": ffmpeg_bin,
        "ffprobe_path": ffprobe_bin,
        "external_tool_dirs_added": list(_EXTERNAL_TOOL_DIRS_ADDED),
        "torch_available": HAS_TORCH,
        "gpu_available": gpu_available,
        "gpu_name": gpu_name,
        "dino_clip_available": bool(HAS_TORCH and HAS_TRANSFORMERS),
        "easyocr_available": HAS_EASYOCR,
        "ark_api_key_set": bool(os.environ.get("ARK_API_KEY")),
        "degraded_visual_backbone": not bool(HAS_TORCH and HAS_TRANSFORMERS),
        "degraded_ocr_backbone": not HAS_EASYOCR,
    }


def compute_ssim_pair(img_a: np.ndarray, img_b: np.ndarray) -> float:
    """计算两张 BGR 图像的结构相似度；尺寸不一致时先对齐再算局部 SSIM（调用方需单独校验原始尺寸）。"""
    if img_a.shape[:2] != img_b.shape[:2]:
        img_b = cv2.resize(img_b, (img_a.shape[1], img_a.shape[0]), interpolation=cv2.INTER_AREA)
    gray_a = cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY)
    score, _ = ssim_metric(gray_a, gray_b, full=True)
    return float(score)


def compute_hsv_bhattacharyya(img_a: np.ndarray, img_b: np.ndarray) -> float:
    """计算两张 BGR 图像 HSV 色彩直方图的巴氏距离（0 为完全一致，1 为完全不同）。"""
    hsv_a = cv2.cvtColor(img_a, cv2.COLOR_BGR2HSV)
    hsv_b = cv2.cvtColor(img_b, cv2.COLOR_BGR2HSV)
    hist_a = cv2.calcHist([hsv_a], [0, 1], None, [32, 32], [0, 180, 0, 256])
    hist_b = cv2.calcHist([hsv_b], [0, 1], None, [32, 32], [0, 180, 0, 256])
    cv2.normalize(hist_a, hist_a, 0, 1, cv2.NORM_MINMAX)
    cv2.normalize(hist_b, hist_b, 0, 1, cv2.NORM_MINMAX)
    return float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_BHATTACHARYYA))


class VisualFeatureExtractor:
    """
    统一语义特征提取器：优先使用 DINOv2 (GPU/CPU)，缺失权重或离线环境下自动降级为
    多尺度结构梯度 + HSV 色彩直方图融合描述符，保证确定性运行且无隐式联网下载阻塞。
    """

    def __init__(self, prefer_gpu: bool = True, model_name: str = "facebook/dinov2-base"):
        self.degraded = True
        self.device = "cpu"
        self.processor = None
        self.model = None

        if HAS_TORCH and HAS_TRANSFORMERS:
            try:
                self.device = "cuda" if (prefer_gpu and torch.cuda.is_available()) else "cpu"
                # local_files_only=True 防止无网或受限环境卡死；若本地已有缓存则直接加载
                self.processor = AutoImageProcessor.from_pretrained(model_name, local_files_only=True)
                self.model = AutoModel.from_pretrained(model_name, local_files_only=True).to(self.device)
                self.model.eval()
                self.degraded = False
            except Exception:
                self.degraded = True

    def embed_bgr(self, img_bgr: np.ndarray) -> np.ndarray:
        if not self.degraded and self.model is not None and self.processor is not None:
            rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)
            inputs = self.processor(images=pil_img, return_tensors="pt").to(self.device)
            with torch.no_grad():
                outputs = self.model(**inputs)
                feat = outputs.last_hidden_state[:, 0, :].squeeze(0).cpu().numpy()
            norm = np.linalg.norm(feat) + 1e-8
            return feat / norm

        # 确定性轻量级结构+色彩语义向量兜底（64x64 空间颜色网格 + Sobel 方向梯度直方图）
        resized = cv2.resize(img_bgr, (64, 64), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
        hist_hs = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256]).flatten()
        hist_hs = hist_hs / (np.linalg.norm(hist_hs) + 1e-8)

        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag, ang = cv2.cartToPolar(gx, gy, angleInDegrees=True)
        bins = np.floor(ang / 22.5).astype(np.int32) % 16
        hog = np.bincount(bins.flatten(), weights=mag.flatten(), minlength=16).astype(np.float32)
        hog = hog / (np.linalg.norm(hog) + 1e-8)

        spatial = cv2.resize(hsv.astype(np.float32) / 255.0, (8, 8)).flatten()
        spatial = spatial / (np.linalg.norm(spatial) + 1e-8)

        vec = np.concatenate([hist_hs * 0.5, hog * 0.3, spatial * 0.2])
        return vec / (np.linalg.norm(vec) + 1e-8)

    def cosine_similarity(self, img_a: np.ndarray, img_b: np.ndarray) -> float:
        va = self.embed_bgr(img_a)
        vb = self.embed_bgr(img_b)
        return float(np.clip(np.dot(va, vb), -1.0, 1.0))

    def cosine_distance(self, img_a: np.ndarray, img_b: np.ndarray) -> float:
        return float(1.0 - self.cosine_similarity(img_a, img_b))


class TextDetector:
    """
    文字检测器：优先使用 EasyOCR，若未安装或离线无权重则自动降级为
    MSER + 高频笔画连通域文本区域启发式检测，并在结果中标注 degraded=True。
    """

    def __init__(self, prefer_gpu: bool = True):
        self.degraded = True
        self.reader = None
        if HAS_EASYOCR:
            try:
                use_gpu = bool(prefer_gpu and HAS_TORCH and torch.cuda.is_available())
                self.reader = easyocr.Reader(["ch_sim", "en"], gpu=use_gpu, download_enabled=False, verbose=False)
                self.degraded = False
            except Exception:
                self.degraded = True

    def detect_text_boxes(
        self, frame_bgr: np.ndarray, reference_bgr: Optional[np.ndarray] = None
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not self.degraded and self.reader is not None:
            results = self.reader.readtext(frame_bgr)
            boxes = []
            for bbox, text, conf in results:
                cleaned = text.strip()
                if conf >= 0.35 and len(cleaned) >= 1:
                    boxes.append({"text": cleaned, "confidence": float(conf), "bbox": bbox})
            return boxes, False

        # 启发式兜底：对比参考图，检测视频帧中新增的高频密集矩形字符团块
        target = frame_bgr
        if reference_bgr is not None and reference_bgr.shape == frame_bgr.shape:
            diff = cv2.absdiff(frame_bgr, reference_bgr)
            gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        else:
            gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)

        grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
        _, bw = cv2.threshold(grad, 45, 255, cv2.THRESH_BINARY)
        # 横向膨胀连接字符成行
        connected = cv2.dilate(bw, cv2.getStructuringElement(cv2.MORPH_RECT, (18, 3)), iterations=1)
        contours, _ = cv2.findContours(connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        h_img, w_img = gray.shape
        boxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            aspect = w / float(h + 1e-5)
            area_ratio = (w * h) / float(w_img * h_img)
            # 典型字幕/水印行宽高比特征
            if 10 <= h <= h_img * 0.12 and aspect >= 3.2 and 0.001 <= area_ratio <= 0.15:
                roi = bw[y : y + h, x : x + w]
                stroke_density = float(np.mean(roi > 0))
                if 0.15 <= stroke_density <= 0.75:
                    boxes.append(
                        {
                            "text": f"[HEURISTIC_TEXT_CANDIDATE_{x}_{y}]",
                            "confidence": round(stroke_density, 3),
                            "bbox": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
                        }
                    )
        return boxes, True


def find_closest_aspect_ratio(width: int, height: int, allowed_ratios: List[str]) -> Tuple[str, float]:
    actual = width / float(height)
    best_ratio = allowed_ratios[0]
    min_diff = float("inf")
    for r_str in allowed_ratios:
        num, den = map(float, r_str.split(":"))
        diff = abs(actual - (num / den))
        if diff < min_diff:
            min_diff = diff
            best_ratio = r_str
    return best_ratio, min_diff


def run_ffprobe_info(video_path: Path) -> Dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video_path}: {proc.stderr}")
    return json.loads(proc.stdout)


def extract_video_frames(video_path: Path, fractions: List[float]) -> List[Tuple[float, int, np.ndarray]]:
    """按归一化比例抽取视频帧，返回 [(fraction, frame_idx, frame_bgr), ...]。"""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video file: {video_path}")
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        raise RuntimeError(f"Video has zero frames: {video_path}")

    extracted: List[Tuple[float, int, np.ndarray]] = []
    for frac in fractions:
        idx = min(max(int(round(frac * (total_frames - 1))), 0), total_frames - 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            raise RuntimeError(f"Failed reading frame {idx} from {video_path}")
        extracted.append((frac, idx, frame))
    cap.release()
    return extracted


def load_pipeline_state(out_dir: Path) -> Dict[str, Any]:
    state_file = out_dir / "pipeline_state.json"
    if state_file.is_file():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {"pages": {}, "relay_edges": [], "meta": {}}


def save_pipeline_state(out_dir: Path, state: Dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    state_file = out_dir / "pipeline_state.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
