#!/usr/bin/env python3
"""汇总一份统一的 qc_report.json：把分散在各处的自检收成一个出口。

聚合来源：
    scene_plan.json           场景划分 / 文字覆盖 / 时长
    prompts.json              提示词契约完整性
    page_*/qa_report.json     清字质量（OCR 残留、非编辑区 SSIM、保护带入侵）
    final_movie.mp4（可选）    ffprobe 时长 / 分辨率 / 帧率

自算一项：跨页调色一致性（同一角色出现的页，两两比 HSV 直方图余弦相似度）。
这是"颜色签名"版的一致性度量，不是 CLIP，也不声称是角色身份识别。

用法：
    python build_qc_report.py --book books/cn01 --scene-plan .tmp/scene_plan_cn01.json \
        --pages-dir .tmp/skill_pages --prompts .tmp/prompts_cn01.json --out .tmp/qc_report.json
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image

DURATION_BAND = (100, 200)
# 同书各页 HSV 直方图余弦的经验水平：cn01 art 0.685 / 补边后的关键帧 0.715。
# 这不是评委用的「风格一致率」，而是同书内部的跑色异常检测：低于 0.60 说明有页明显变色。
CONSISTENCY_MIN = 0.60


def load_json(p: Path) -> dict | None:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def hsv_hist(img_path: Path, bins: tuple[int, int, int] = (12, 4, 4)) -> list[float]:
    """HSV 三维直方图（归一化），用 numpy 分箱。"""
    import numpy as np

    with Image.open(img_path) as im:
        arr = np.asarray(im.convert("HSV").resize((160, 160)), dtype=np.int32)
    hb, sb, vb = bins
    h = arr[..., 0] * hb // 256
    s_ = arr[..., 1] * sb // 256
    v = arr[..., 2] * vb // 256
    idx = (v * sb + s_) * hb + h
    counts = np.bincount(idx.ravel(), minlength=hb * sb * vb).astype(float)
    total = counts.sum() or 1.0
    return (counts / total).tolist()


def cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


def cross_page_consistency(pages_dir: Path, book_dir: Path) -> dict:
    """同一角色出现的页，两两比较整体调色（HSV 直方图）。"""
    chars_file = Path(book_dir) / "reader" / "characters" / "characters.json"
    if not chars_file.is_file():
        return {"ok": None, "score": None, "pairs": 0, "reason": "没有 characters.json"}
    data = json.loads(chars_file.read_text(encoding="utf-8"))
    chars = data.get("characters") if isinstance(data, dict) else data
    hist_cache: dict[str, list[float]] = {}
    sims = []
    for c in chars or []:
        pids = [p for p in (c.get("pages") or []) if (Path(pages_dir) / p / "clean_keyframe.png").is_file()]
        for i in range(len(pids)):
            for j in range(i + 1, len(pids)):
                for pid in (pids[i], pids[j]):
                    if pid not in hist_cache:
                        hist_cache[pid] = hsv_hist(Path(pages_dir) / pid / "clean_keyframe.png")
                sims.append(cosine(hist_cache[pids[i]], hist_cache[pids[j]]))
    score = round(sum(sims) / len(sims), 4) if sims else None
    return {"ok": (score is not None and score >= CONSISTENCY_MIN), "score": score,
            "pairs": len(sims), "min": CONSISTENCY_MIN, "method": "hsv_histogram_cosine"}


def probe_movie(path: Path) -> dict:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return {"ok": False, "reason": "ffprobe 失败"}
        d = json.loads(r.stdout)
        st = (d.get("streams") or [{}])[0]
        num, den = (st.get("r_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den or 1)
        dur = float((d.get("format") or {}).get("duration") or 0)
        return {"ok": True, "width": st.get("width"), "height": st.get("height"),
                "fps": round(fps, 3), "duration_sec": round(dur, 2)}
    except Exception as exc:
        return {"ok": False, "reason": repr(exc)}


def main() -> int:
    ap = argparse.ArgumentParser(description="统一自检报告")
    ap.add_argument("--book", required=True)
    ap.add_argument("--scene-plan", required=True)
    ap.add_argument("--pages-dir", required=True)
    ap.add_argument("--prompts", default="")
    ap.add_argument("--movie", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    plan = load_json(Path(a.scene_plan)) or {}
    prompts = load_json(Path(a.prompts)) if a.prompts else None
    pages_dir = Path(a.pages_dir)

    checks = []
    problems = []

    def add(cid: str, ok: bool | None, detail: str, **extra) -> None:
        checks.append({"id": cid, "ok": ok, "detail": detail, **extra})
        if ok is False:
            problems.append(f"{cid}: {detail}")

    # 1 场景与文字覆盖
    cov = plan.get("coverage") or {}
    total_pages = cov.get("pages_total") or 0
    add("scene_plan", bool(plan.get("segments")),
        f"{plan.get('scene_count', 0)} 场 / {total_pages} 页")
    add("text_coverage", (cov.get("text_pct") == 1.0 and cov.get("pages_assigned") == total_pages),
        f"文字覆盖 {cov.get('text_pct', 0):.1%}，页覆盖 {cov.get('pages_assigned', 0)}/{total_pages}")

    # 2 时长
    total_sec = int(plan.get("estimated_total_sec") or 0)
    lo, hi = DURATION_BAND
    add("duration", lo <= total_sec <= hi, f"{total_sec}s（官方 {lo}–{hi}s）")

    # 3 关键帧 QC
    qa_files = sorted(pages_dir.glob("*/qa_report.json"))
    if qa_files:
        resids, ssims, intrus = [], [], []
        fails = []
        for f in qa_files:
            q = load_json(f) or {}
            resids.append(int(q.get("ocr_residual_count") or 0))
            ssims.append(float(q.get("non_edited_ssim") or 0))
            intrus.append(int(q.get("protected_zone_intrusion_px") or 0))
            if str(q.get("qa_verdict") or "").upper() != "PASS":
                fails.append(f.parent.name)
        ok = not fails
        add("keyframe_qa", ok,
            f"{len(qa_files) - len(fails)}/{len(qa_files)} 页 PASS；残留合计 {sum(resids)}；"
            f"SSIM 最低 {min(ssims):.4f}；入侵最大 {max(intrus)}px",
            failed_pages=fails)
    else:
        add("keyframe_qa", None, "没有 page_*/qa_report.json")

    # 4 提示词契约
    if prompts is not None:
        segs = prompts.get("segments") or []
        bad = [s["scene_id"] for s in segs
               if not s.get("prompt") or not s.get("first_frame") or not s.get("negative")]
        add("prompts", not bad and len(segs) == plan.get("scene_count"),
            f"{len(segs)} 场，缺字段 {bad or '无'}")
    else:
        add("prompts", None, "没给 --prompts")

    # 5 跨页调色一致性
    cons = cross_page_consistency(pages_dir, Path(a.book))
    add("consistency", cons["ok"],
        f"同书调色相似度 {cons['score']}（{cons['pairs']} 对，异常阈值 {CONSISTENCY_MIN}；"
        f"实测基线 cn01≈0.69）"
        if cons.get("score") is not None else f"无法计算：{cons.get('reason')}",
        method=cons.get("method"))

    # 6 成片（可选）
    movie = Path(a.movie) if a.movie else pages_dir.parent / "final_movie.mp4"
    if movie.is_file():
        pr = probe_movie(movie)
        if pr.get("ok"):
            ok = (pr["width"] >= 720 and pr["height"] >= 720 and pr["fps"] >= 24)
            add("movie", ok, f"{pr['width']}x{pr['height']} {pr['fps']}fps {pr['duration_sec']}s")
        else:
            add("movie", False, pr.get("reason", "探测失败"))
    else:
        add("movie", None, "成片不存在（dry-run 阶段正常）")

    verdict = "PASS" if not problems else ("FAIL" if any(c["ok"] is False for c in checks) else "WARN")
    report = {
        "book_id": plan.get("book_id"),
        "verdict": verdict,
        "checks": checks,
        "problems": problems,
        "sources": {"scene_plan": a.scene_plan, "prompts": a.prompts or None},
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for c in checks:
        mark = {True: "PASS", False: "FAIL", None: "SKIP"}[c["ok"]]
        print(f"[{mark}] {c['id']:<14} {c['detail']}")
    print(f"\n结论：{verdict}")
    return 0 if verdict == "PASS" else (1 if verdict == "FAIL" else 2)


if __name__ == "__main__":
    sys.exit(main())
