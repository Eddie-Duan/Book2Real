# -*- coding: utf-8 -*-
"""Post-generation gates. Hard checks fail closed; soft checks report numbers.

No model downloads: MobileNetV2 comes from torchvision's public checkpoint,
YuNet ONNX lives in .tmp/models (downloaded once), RapidOCR is local.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from common import (ROOT, asr, audio_peak_db, contains_approx, ffmpeg, ffprobe,
                    first_frame, jwrite, last_frame, normalize, overlap)

_MODELS: dict = {}


def _lazy():
    if _MODELS:
        return _MODELS
    import torch
    import torch.nn as nn
    from torchvision.models import MobileNet_V2_Weights, mobilenet_v2
    model = mobilenet_v2(weights=MobileNet_V2_Weights.DEFAULT).eval()
    feat = nn.Sequential(*list(model.features), nn.AdaptiveAvgPool2d(1))
    _MODELS["tf"] = MobileNet_V2_Weights.DEFAULT.transforms()
    _MODELS["feat"] = feat
    _MODELS["torch"] = torch
    yunet = ROOT / ".tmp" / "models" / "yunet.onnx"
    _MODELS["yunet"] = str(yunet) if yunet.exists() else ""
    from rapidocr_onnxruntime import RapidOCR
    _MODELS["ocr"] = RapidOCR()
    return _MODELS


def _emb(path_or_img) -> np.ndarray:
    m = _lazy()
    from PIL import Image
    img = path_or_img if isinstance(path_or_img, Image.Image) else Image.open(path_or_img).convert("RGB")
    t = m["tf"](img).unsqueeze(0)
    with m["torch"].no_grad():
        v = m["feat"](t).flatten()
    v = v / v.norm()
    return v.numpy()


def _cos(a, b) -> float:
    return float(np.dot(a, b))


def _ssim(a_path: Path, b_path: Path) -> float:
    from skimage.metrics import structural_similarity as ssim
    a = cv2.imread(str(a_path), cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(str(b_path), cv2.IMREAD_GRAYSCALE)
    if a is None or b is None:
        return 0.0
    b = cv2.resize(b, (a.shape[1], a.shape[0]))
    return float(ssim(a, b, data_range=255))


def extract_frames(video: Path, fps: float = 1.0) -> list[Path]:
    d = Path(tempfile.mkdtemp(prefix="ot_frames_"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video),
                    "-vf", f"fps={fps},scale=960:-1", str(d / "f_%04d.jpg")], check=True)
    return sorted(d.glob("*.jpg"))


def _faces(img_path: Path) -> list[tuple[int, int, int, int, float]]:
    m = _lazy()
    if not m["yunet"]:
        return []
    img = cv2.imread(str(img_path))
    if img is None:
        return []
    small = cv2.resize(img, (1280, 720))
    det = cv2.FaceDetectorYN_create(m["yunet"], "", (1280, 720), 0.7, 0.3, 5000)
    _, faces = det.detect(small)
    if faces is None:
        return []
    return [(int(f[0]), int(f[1]), int(f[2]), int(f[3]), float(f[14])) for f in faces]


def _face_emb(img_path: Path, box: tuple[int, int, int, int]) -> np.ndarray | None:
    from PIL import Image
    img = cv2.cvtColor(cv2.resize(cv2.imread(str(img_path)), (1280, 720)), cv2.COLOR_BGR2RGB)
    x, y, w, h = box
    pad = int(max(w, h) * 0.35)
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(1280, x + w + pad), min(720, y + h + pad)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    return _emb(Image.fromarray(crop))


def _flow_scale(f1: Path, f2: Path) -> dict:
    """Estimate per-region affine scale between two frames."""
    a = cv2.imread(str(f1), cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(str(f2), cv2.IMREAD_GRAYSCALE)
    if a is None or b is None:
        return {"ok": False}
    h, w = a.shape
    pts = cv2.goodFeaturesToTrack(a, maxCorners=400, qualityLevel=0.01, minDistance=12)
    if pts is None or len(pts) < 20:
        return {"ok": False}
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(a, b, pts, None)
    if nxt is None:
        return {"ok": False}
    p0 = pts[st.flatten() == 1].reshape(-1, 2)
    p1 = nxt[st.flatten() == 1].reshape(-1, 2)
    if len(p0) < 20:
        return {"ok": False}
    M, _ = cv2.estimateAffinePartial2D(p0, p1, method=cv2.LMEDS)
    if M is None:
        return {"ok": False}
    scale = math.sqrt(abs(M[0, 0] * M[1, 1] - M[0, 1] * M[1, 0]))
    regions = {}
    for name, (x0, y0, x1, y1) in {
            "center": (w // 3, h // 3, 2 * w // 3, 2 * h // 3),
            "tl": (0, 0, w // 3, h // 3), "tr": (2 * w // 3, 0, w, h // 3),
            "bl": (0, 2 * h // 3, w // 3, h), "br": (2 * w // 3, 2 * h // 3, w, h)}.items():
        sel = (p0[:, 0] >= x0) & (p0[:, 0] < x1) & (p0[:, 1] >= y0) & (p0[:, 1] < y1) & \
              (p1[:, 0] >= x0) & (p1[:, 0] < x1) & (p1[:, 1] >= y0) & (p1[:, 1] < y1)
        if sel.sum() >= 12:
            Mr, _ = cv2.estimateAffinePartial2D(p0[sel], p1[sel], method=cv2.LMEDS)
            if Mr is not None:
                regions[name] = round(math.sqrt(abs(Mr[0, 0] * Mr[1, 1] - Mr[0, 1] * Mr[1, 0])), 4)
    return {"ok": True, "scale": round(float(scale), 4), "regions": regions}


def check_spec(video: Path, expected: float, resolution: str = "720p") -> dict:
    info = ffprobe(video)
    v = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    a = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    dur = float(info.get("format", {}).get("duration") or 0)
    failures = []
    if not v:
        failures.append({"code": "spec.no_video", "fix": "重生成；文件无视频流"})
    else:
        want = (854, 480) if resolution == "480p" else (1280, 720)
        if (v[0].get("width"), v[0].get("height")) != want:
            failures.append({"code": "spec.resolution", "fix": f"要求 {want[0]}x{want[1]}（{resolution}）；重生成，不要后期放大"})
        if v[0].get("r_frame_rate") != "24/1":
            failures.append({"code": "spec.fps", "fix": "重生成 24fps"})
    if not a:
        failures.append({"code": "spec.no_audio", "fix": "generate_audio=true；不要静音"})
    if abs(dur - expected) > 1.5:
        failures.append({"code": "spec.duration", "fix": f"要求 {expected}s，实际 {dur:.2f}s，重生成"})
    return {"passed": not failures, "failures": failures,
            "metrics": {"duration": dur, "width": v[0]["width"] if v else None,
                        "height": v[0]["height"] if v else None,
                        "fps": v[0].get("r_frame_rate") if v else None,
                        "audio": bool(a)}}


def check_audio(video: Path) -> dict:
    peak = audio_peak_db(video)
    failures = []
    if peak is None:
        failures.append({"code": "audio.peak", "fix": "音轨无法读取；重生成带音频版本"})
    elif peak < -25:
        failures.append({"code": "audio.silent", "fix": f"峰值 {peak:.1f} dB 过低；检查旁白/对白是否被静音"})
    return {"passed": not failures, "failures": failures, "metrics": {"peak_db": peak}}


def check_asr(video: Path, chunk: dict, language: str = "zh") -> dict:
    try:
        got = asr(video, language)
    except Exception as exc:  # noqa: BLE001
        return {"passed": False, "failures": [{"code": "asr.error", "fix": f"ASR 失败：{exc}"}],
                "metrics": {}}
    text = got.get("text") or ""
    lines = [n.get("text", "") for n in chunk.get("narration") or []] + \
            [d.get("text", "") for d in chunk.get("dialogue") or []]
    per = []
    failures = []
    for line in lines:
        score = 1.0 if contains_approx(text, line, 0.80) else overlap(text, line)
        per.append({"line": line, "score": round(score, 3)})
        if score < 0.55:
            failures.append({"code": "asr.line", "fix": f"台词没被正确说出：{line}；把台词放回 [Audio]，逐字保留"})
    coverage = (sum(p["score"] for p in per) / len(per)) if per else 0.0
    if coverage < 0.55:
        failures.append({"code": "asr.total", "fix": "整段旁白/对白覆盖不足；减少台词、放慢语速"})
    return {"passed": not failures, "failures": failures,
            "metrics": {"asr": text, "per_line": per, "line_coverage": round(coverage, 3)}}


def check_text(frames: list[Path]) -> dict:
    m = _lazy()
    hits = []
    for f in frames[::2]:
        res, _ = m["ocr"](str(f))
        for item in res or []:
            box, txt, conf = item[0], item[1], float(item[2])
            if conf < 0.55:
                continue
            if len(normalize(txt)) < 2:
                continue
            hits.append({"frame": f.name, "text": txt, "conf": round(conf, 3)})
    bad = [h for h in hits if any(k in h["text"] for k in ("仅供", "北京市大学生", "请勿外传", "纸书智伴"))]
    failures = []
    if hits:
        failures.append({"code": "text.detected", "fix": "画面出现文字；在提示词强调无字幕/文字/水印，并重生成"})
    if bad:
        failures.append({"code": "text.watermark", "fix": "画面出现官方水印文字；重生成，禁止原书页"})
    return {"passed": not failures, "failures": failures,
            "metrics": {"hits": hits[:12], "count": len(hits)}}


def check_raw_page(frames: list[Path], book_pages: list[Path]) -> dict:
    from skimage.metrics import structural_similarity as ssim
    refs = []
    for p in book_pages:
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            refs.append(cv2.resize(img, (320, 160)))
    worst = 0.0
    worst_frame = None
    for f in frames[::2]:
        img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        g = cv2.resize(img, (320, 160))
        for r in refs:
            s = float(ssim(g, r, data_range=255))
            if s > worst:
                worst, worst_frame = s, f.name
    failures = []
    if worst > 0.80:
        failures.append({"code": "raw_page", "fix": "画面接近原书页/分格图；必须用重新生成的全幅场景板，重生成"})
    return {"passed": not failures, "failures": failures,
            "metrics": {"max_page_ssim": round(worst, 3), "frame": worst_frame}}


def check_seam(prev: Path | None, cur: Path, out: Path) -> dict:
    if prev is None:
        return {"passed": True, "failures": [], "metrics": {"seam": None}}
    a = last_frame(prev, out / "_seam_prev.png")
    b = first_frame(cur, out / "_seam_cur.png")
    s = _ssim(a, b)
    mean = float(cv2.imread(str(b), cv2.IMREAD_GRAYSCALE).mean()) if b.exists() else 0
    failures = []
    if s < 0.90:
        failures.append({"code": "seam.ssim", "fix": f"接缝 SSIM {s:.3f}<0.90；上一段结尾和本段开头不是同一动作，重生成本段"})
    if mean < 12:
        failures.append({"code": "seam.black", "fix": "本段开头接近黑场；重生成，从上一段末帧继续"})
    return {"passed": not failures, "failures": failures,
            "metrics": {"seam_ssim": round(s, 4), "first_mean": round(mean, 1)}}


def check_digital_zoom(video: Path, frames: list[Path] | None = None) -> dict:
    frames = frames or extract_frames(video, fps=1.0)
    verdicts = []
    for i in range(0, max(1, len(frames) - 1), 2):
        r = _flow_scale(frames[i], frames[min(i + 1, len(frames) - 1)])
        if r.get("ok"):
            verdicts.append(r)
    if not verdicts:
        return {"passed": True, "failures": [], "metrics": {"note": "flow unavailable"}}
    max_uniform = 1.0
    max_parallax = 0.0
    for r in verdicts:
        regs = r.get("regions") or {}
        if len(regs) >= 3:
            vals = list(regs.values())
            spread = max(vals) - min(vals)
            mean = sum(vals) / len(vals)
            max_parallax = max(max_parallax, spread)
            if spread < 0.03:
                max_uniform = max(max_uniform, abs(mean - 1.0) + 1.0)
    failures = []
    if max_uniform > 1.10:
        failures.append({"code": "digital_zoom",
                         "fix": "检测到整幅均匀缩放（无视差），像数字 zoom；改成物理机位移动或切镜"})
    return {"passed": not failures, "failures": failures,
            "metrics": {"uniform_scale_max": round(max_uniform, 4),
                        "parallax_max": round(max_parallax, 4),
                        "samples": len(verdicts)}}


def check_style_cast(frames: list[Path], head_refs: dict[str, Path],
                     plate: Path | None) -> dict:
    if not head_refs or not frames:
        return {"passed": True, "failures": [], "metrics": {"note": "no refs"}}
    ref_emb = {k: _emb(v) for k, v in head_refs.items() if Path(v).exists()}
    plate_emb = _emb(plate) if plate and Path(plate).exists() else None
    style = []
    face_records = []
    for f in frames[::2]:
        if plate_emb is not None:
            style.append(_cos(_emb(f), plate_emb))
        for box in _faces(f):
            e = _face_emb(f, box[:4])
            if e is None:
                continue
            sims = {k: _cos(e, v) for k, v in ref_emb.items()}
            best = max(sims, key=sims.get)
            face_records.append({"frame": f.name, "best": best,
                                 "score": round(sims[best], 3),
                                 "all": {k: round(v, 3) for k, v in sims.items()}})
    metrics = {"style_min": round(min(style), 3) if style else None,
               "style_mean": round(sum(style) / len(style), 3) if style else None,
               "faces": len(face_records),
               "face_best_mean": round(sum(r["score"] for r in face_records) / len(face_records), 3)
               if face_records else None,
               "face_samples": face_records[:10]}
    # soft gate: only gross drift is a failure, exact identity is reviewed on contact sheet
    failures = []
    if metrics["style_min"] is not None and metrics["style_min"] < 0.35:
        failures.append({"code": "style.drift", "fix": "画风明显漂移；加强风格 DNA 和场景板参考"})
    return {"passed": not failures, "failures": failures, "metrics": metrics}


def gate_chunk(chunk: dict, video: Path, out: Path, prev: Path | None,
               book_pages: list[Path], head_refs: dict[str, Path],
               plate: Path | None, language: str = "zh", resolution: str = "720p") -> dict:
    frames = extract_frames(video, 1.0)
    checks = {
        "spec": check_spec(video, float(chunk["duration"]), resolution),
        "audio": check_audio(video),
        "asr": check_asr(video, chunk, language),
        "text_free": check_text(frames),
        "no_raw_page": check_raw_page(frames, book_pages),
        "seam": check_seam(prev, video, out),
        "digital_zoom": check_digital_zoom(video, frames),
        "style_cast": check_style_cast(frames[::2], head_refs, plate),
    }
    failures = []
    for name, c in checks.items():
        for f in c.get("failures") or []:
            failures.append({"gate": name, **f})
    verdict = "PASS" if not failures else "FAIL"
    report = {"chunk_id": chunk["id"], "video": str(video), "verdict": verdict,
              "checks": checks, "failures": failures}
    jwrite(out / f"{chunk['id']}_gate.json", report)
    return report