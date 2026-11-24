#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整本绘本一次生成的确定性串联器（relay orchestrator）。

把五个 skill 之间的"接力"从"读文档照做"变成一条可执行的命令链：

    plan_scenes -> build_bridge_frames -> preflight --plan -> generate
    -> poll_download -> qc -> concat -> book_timeline

设计原则：
- **失败即停**：任何一步非零退出，整条链立刻中止（整本要拼成一条片，缺一段就拼不出来）。
- **文件即契约**：步骤之间只通过磁盘产物交接（scene_plan.json / preflight.json /
  pipeline_state.json / qc.json），不靠内存状态，因此可以从任意一步续跑。
- **dry-run 免费**：--dry-run 只跑到"打印生成 payload"，不调 API、不花钱。

用法：
    python run_pipeline.py --list
    python run_pipeline.py --pages-dir pages --out-dir book_out --dry-run
    python run_pipeline.py --pages-dir pages --out-dir book_out --resolution 1080p --yes

需要用它所在的 venv 解释器运行（依赖 relay 的 requirements）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

# Windows 控制台默认 GBK，子进程输出里的 ¥ / 表情会直接抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BITMAP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


# --------------------------------------------------------------------------- utils
def find_scene_scripts(explicit: Path | None) -> Path:
    if explicit:
        p = explicit.expanduser().resolve()
        if (p / "plan_scenes.py").is_file():
            return p
        raise SystemExit(f"[pipeline] --scene-scripts 下没有 plan_scenes.py: {p}")

    env = os.environ.get("SCENE_DIRECTOR_SCRIPTS")
    if env:
        p = Path(env).expanduser().resolve()
        if (p / "plan_scenes.py").is_file():
            return p

    for base in (REPO / "_build", REPO):
        for name in ("picturebook-scene-director", "picturebook-scene-director-main"):
            cand = base / name / "scripts"
            if (cand / "plan_scenes.py").is_file():
                return cand
    raise SystemExit(
        "[pipeline] 找不到 picturebook-scene-director/scripts；"
        "请用 --scene-scripts 指定，或设置 SCENE_DIRECTOR_SCRIPTS。"
    )


def find_relay_root(explicit: Path | None) -> Path:
    if explicit:
        p = explicit.expanduser().resolve()
    else:
        p = REPO / "seedance-relay-producer"
    if not (p / "scripts" / "preflight.py").is_file():
        raise SystemExit(f"[pipeline] 不是 relay 根目录（缺 scripts/preflight.py）: {p}")
    return p


def read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class Ctx:
    pages_dir: Path
    out_dir: Path
    plan_path: Path
    relay_out: Path
    resolution: str
    dry_run: bool
    yes: bool
    keep_going: bool
    poll_timeout_sec: int
    scene_scripts: Path
    relay_root: Path
    target_seconds: int = 0
    book: str = ""
    narration: Path | None = None
    build_narration: bool = False


def run(ctx: Ctx, label: str, argv: list[str], cwd: Path) -> None:
    printable = " ".join(str(a) for a in argv)
    print(f"\n=== [{label}] {printable}")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [str(a) for a in argv],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = out.rstrip().splitlines()
    tail = lines[-25:]
    if tail:
        print("\n".join("    " + line for line in tail))
    if proc.returncode != 0:
        print(f"    [FAILED] {label} exit={proc.returncode}")
        if not ctx.keep_going:
            raise SystemExit(proc.returncode)
    else:
        print(f"    [ok] {label}")


# --------------------------------------------------------------------------- steps
def gate_keyframe_text(pages_dir: Path) -> None:
    """S1 出口硬门：关键帧整帧读不出字才算过关。

    只看原始文本框会自证清白（页脚/页码/水印不在框里），这些字会被 Seedance 原样动起来，
    成片看起来就是"原书页在动"。
    """
    bad: list[str] = []
    for pdir in sorted(pages_dir.iterdir()):
        qa = pdir / "qa_report.json"
        if not qa.is_file():
            continue
        try:
            data = read_json(qa)
        except Exception:
            continue
        gate = data.get("text_gate") or {}
        if gate.get("passed") is False:
            sample = " / ".join(gate.get("sample") or [])[:60]
            bad.append(f"{pdir.name}({gate.get('count')} 处：{sample})")
    if bad:
        raise SystemExit(
            "[pipeline] 关键帧仍能读出文字，禁止送进 Seedance（会把原书页原样动起来）："
            + "; ".join(bad[:6])
        )
    print("    [gate] 关键帧整帧无字（text_gate 全 PASS）")


def step_plan(ctx: Ctx) -> None:
    run(ctx, "S3 分镜（plan_scenes）", [
        sys.executable, ctx.scene_scripts / "plan_scenes.py",
        "--pages-dir", ctx.pages_dir,
        "--out-dir", ctx.out_dir,
    ], cwd=ctx.scene_scripts)


def step_duration(ctx: Ctx) -> None:
    if not ctx.target_seconds:
        return
    run(ctx, "S3c 时长对齐（ensure_duration）", [
        sys.executable, Path(__file__).with_name("ensure_duration.py"),
        "--plan", str(ctx.out_dir / "scene_plan.json"),
        "--target", str(ctx.target_seconds),
    ], cwd=Path(__file__).parent)


def step_bridge(ctx: Ctx) -> None:
    run(ctx, "S3b 桥接帧（build_bridge_frames）", [
        sys.executable, ctx.scene_scripts / "build_bridge_frames.py",
        "--plan", ctx.plan_path,
        "--pages-dir", ctx.pages_dir,
        "--out-dir", ctx.out_dir,
    ], cwd=ctx.scene_scripts)


def infer_book_dir(ctx: Ctx) -> Path | None:
    """从页 manifest 的 book_id 反查 books/<id>；--book 优先。"""
    if ctx.book:
        p = Path(ctx.book).expanduser().resolve()
        return p if p.is_dir() else None
    for pdir in sorted(ctx.pages_dir.iterdir()):
        mf = pdir / "page_manifest.json"
        if not mf.is_file():
            continue
        try:
            bid = read_json(mf).get("book_id")
        except Exception:
            bid = None
        if bid:
            cand = REPO.parent / "books" / str(bid)
            if cand.is_dir():
                return cand
    return None


def step_enrich(ctx: Ctx) -> None:
    """S3d：把段落提示词升级成五段式（[Camera] + 角色锚点 + 负向约束）。"""
    argv = [
        sys.executable, Path(__file__).with_name("enrich_seg_prompts.py"),
        "--plan", str(ctx.plan_path),
        "--pages-dir", str(ctx.pages_dir),
        "--out-dir", str(ctx.out_dir),
    ]
    book_dir = infer_book_dir(ctx)
    if book_dir:
        argv += ["--book", str(book_dir)]
    run(ctx, "S3d 提示词增强（enrich_seg_prompts）", argv, cwd=Path(__file__).parent)
    summary = ctx.out_dir / "prompt_enrich.json"
    if not summary.is_file():
        raise SystemExit(f"[pipeline] 提示词增强没写出 {summary}")
    data = read_json(summary)
    print(f"    [gate] 角色锚点覆盖 {data.get('segments_with_anchors')}/{len(data.get('segments') or [])} 段")


def step_preflight(ctx: Ctx) -> None:
    argv = [
        sys.executable, "scripts/preflight.py",
        "--plan", ctx.plan_path,
        "--pages-dir", ctx.pages_dir,
        "--out-dir", ctx.relay_out,
        "--resolution", ctx.resolution,
    ]
    if ctx.dry_run:
        argv.append("--dry-run")
    run(ctx, "S4a 生成前检查（preflight --plan）", argv, cwd=ctx.relay_root)

    pf = ctx.relay_out / "preflight.json"
    if not pf.is_file():
        raise SystemExit(f"[pipeline] preflight 没写出 {pf}，中止。")
    data = read_json(pf)
    if data.get("mode") != "PLAN":
        raise SystemExit("[pipeline] preflight.json 不是 PLAN 模式，拒绝继续（避免按错模式跑整本）。")
    if not data.get("ready_for_generation"):
        blocked = [
            p.get("page_id") for p in data.get("pages", [])
            if str(p.get("status")) != "PREFLIGHT_READY"
        ]
        raise SystemExit(
            "[pipeline] 有段未就绪，整本一次生成不允许只出一部分。阻断段: "
            + ", ".join(str(b) for b in blocked[:10])
        )
    print(f"    [gate] ready_for_generation=true, segments={len(data.get('pages', []))}")


def step_generate(ctx: Ctx) -> None:
    argv = [
        sys.executable, "scripts/generate.py",
        "--out-dir", ctx.relay_out,
        "--resolution", ctx.resolution,
    ]
    if ctx.dry_run:
        argv.append("--dry-run")
    elif ctx.yes:
        argv.append("--yes")
    run(ctx, "S4b 提交生成（generate）", argv, cwd=ctx.relay_root)
    if ctx.dry_run:
        return
    if not os.environ.get("ARK_API_KEY"):
        print("    [warn] 未检测到 ARK_API_KEY；若 generate 已报错，请在终端设置后重跑。")
    append_ledger_for_new_costs(ctx)


def append_ledger_for_new_costs(ctx: Ctx) -> None:
    """把 skills 产线新增的 Seedance 费用同步进总账 runs/cloud/ledger.jsonl。

    产线自己记 cost.csv，总账只认 ledger.jsonl；不同步的话预算护栏与说明书
    里"已用 X 元"会少算。用游标文件保证不重复记账。
    """
    cost_csv = ctx.relay_out / "cost.csv"
    if not cost_csv.is_file():
        return
    ledger = REPO.parent / "runs" / "cloud" / "ledger.jsonl"
    cursor_file = ctx.relay_out / ".ledger_cursor"
    try:
        done = int(cursor_file.read_text(encoding="utf-8").strip() or 0)
    except Exception:
        done = 0
    lines = [ln for ln in cost_csv.read_text(encoding="utf-8").splitlines() if ln.strip()]
    header, rows = lines[0], lines[1:]
    if len(rows) <= done:
        return
    ledger.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    with open(ledger, "a", encoding="utf-8") as fh:
        for row in rows[done:]:
            parts = row.split(",")
            if len(parts) < 9:
                continue
            ts, page_id, attempt, task_id, model, resolution, dur, _lvl, cost = parts[:9]
            entry = {
                "ts": ts.replace("+00:00", "").strip(),
                "provider": "seedance",
                "model": model,
                "cny": float(cost or 0),
                "units": {"seconds": int(float(dur or 0)), "task": task_id, "segment": page_id,
                          "attempt": attempt, "resolution": resolution},
                "note": "skills pipeline (seedance-relay-producer)",
            }
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            added += 1
    cursor_file.write_text(str(len(rows)), encoding="utf-8")
    if added:
        print(f"    [ledger] 新记账 {added} 条 -> {ledger}")


def _polling_count(ctx: Ctx) -> int:
    state_path = ctx.relay_out / "pipeline_state.json"
    if not state_path.is_file():
        return 0
    state = read_json(state_path)
    return sum(
        1 for p in state.get("pages", {}).values()
        if str(p.get("status")) == "POLLING"
    )


def step_poll(ctx: Ctx) -> None:
    if ctx.dry_run:
        print("\n=== [S4c 轮询下载] dry-run 跳过")
        return
    deadline = time.time() + ctx.poll_timeout_sec
    round_no = 0
    while True:
        round_no += 1
        run(ctx, f"S4c 轮询下载 #{round_no}", [
            sys.executable, "scripts/poll_download.py",
            "--out-dir", ctx.relay_out,
        ], cwd=ctx.relay_root)
        pending = _polling_count(ctx)
        if pending == 0:
            print("    [ok] 没有仍在生成的任务")
            return
        if time.time() > deadline:
            raise SystemExit(
                f"[pipeline] 轮询超时（{ctx.poll_timeout_sec}s），仍有 {pending} 段在生成；"
                "稍后重跑本命令会从断点继续。"
            )
        print(f"    ... {pending} 段仍在生成，10s 后重试")
        time.sleep(10)


def step_qc(ctx: Ctx) -> None:
    if ctx.dry_run:
        print("\n=== [S4d 质检] dry-run 跳过")
        return
    run(ctx, "S4d 质检（qc）", [
        sys.executable, "scripts/qc.py",
        "--out-dir", ctx.relay_out,
    ], cwd=ctx.relay_root)

    failed = []
    for qc_file in sorted(ctx.relay_out.glob("seg_*/qc.json")):
        try:
            verdict = str(read_json(qc_file).get("verdict", "")).upper()
        except Exception:
            verdict = "UNREADABLE"
        if verdict != "PASS":
            failed.append(f"{qc_file.parent.name}:{verdict}")
    if failed:
        raise SystemExit(
            "[pipeline] 质检未通过，禁止拼接（整本不可二次编辑，必须修完再拼）: "
            + ", ".join(failed[:10])
        )
    print("    [gate] 全部段 qc PASS")
    degraded = []
    for qc_file in sorted(ctx.relay_out.glob("seg_*/qc.json")):
        try:
            data = read_json(qc_file)
        except Exception:
            continue
        det = data.get("degraded_detectors") or {}
        cons = (data.get("checks") or {}).get("temporal_consistency") or {}
        if det.get("visual_feature_degraded") or cons.get("applicable") is False:
            degraded.append(qc_file.parent.name)
    if degraded:
        print(f"    [warn] 人物/背景一致性检测降级或跳过（缺 DINOv2）：{len(degraded)} 段 "
              f"→ {', '.join(degraded[:6])}{'…' if len(degraded) > 6 else ''}")
        print("          当前一致性证据只有首/尾帧锚点 SSIM；要硬证据请装 DINOv2/CLIP 后重跑 qc。")


def step_narration(ctx: Ctx) -> None:
    """S4e：产出/确认旁白轨。--build-narration 才会真调 TTS（MiniMax，按字符计费，带缓存）。"""
    if ctx.build_narration and ctx.dry_run:
        print("\n=== [S4e 旁白] dry-run 跳过 TTS 合成")
        return
    if ctx.build_narration:
        book_dir = infer_book_dir(ctx)
        if book_dir is None:
            raise SystemExit("[pipeline] --build-narration 需要 --book，或页 manifest 里有 book_id")
        build_audio = ctx.scene_scripts.parent.parent / "picturebook-scene-grouper" / "scripts" / "build_audio_plan.py"
        plan_out = ctx.out_dir / "audio_plan.json"
        if not build_audio.is_file():
            raise SystemExit(f"[pipeline] 找不到 {build_audio}（--scene-scripts 指错了？）")
        run(ctx, "S4e1 声音计划（build_audio_plan）", [
            sys.executable, build_audio,
            "--book", str(book_dir),
            "--scene-plan", str(ctx.plan_path),
            "--out", str(plan_out),
        ], cwd=build_audio.parent)
        narration = ctx.out_dir / "narration.mp3"
        argv = [
            sys.executable, Path(__file__).with_name("build_narration.py"),
            "--audio-plan", str(plan_out),
            "--out", str(narration),
        ]
        run(ctx, "S4e2 旁白合成（build_narration）", argv, cwd=Path(__file__).parent)
        if narration.is_file():
            ctx.narration = narration
    if ctx.narration is None:
        print("\n=== [S4e 旁白] 未指定旁白轨：成片将只有静音轨")
        return
    if not Path(ctx.narration).is_file():
        raise SystemExit(f"[pipeline] 旁白轨不存在: {ctx.narration}")
    print(f"\n=== [S4e 旁白] 使用 {ctx.narration}")


def step_concat(ctx: Ctx) -> None:
    if ctx.dry_run:
        print("\n=== [S5 拼接] dry-run 跳过")
        return
    argv = [
        sys.executable, "scripts/concat.py",
        "--out-dir", ctx.relay_out,
    ]
    if ctx.narration and Path(ctx.narration).is_file():
        argv += ["--audio", str(ctx.narration)]
    run(ctx, "S5 拼接（concat）", argv, cwd=ctx.relay_root)
    movie = ctx.relay_out / "final_movie.mp4"
    if movie.is_file():
        print(f"    [out] {movie}")
    else:
        raise SystemExit(f"[pipeline] 拼接结束但没找到 {movie}")
    spec_file = ctx.relay_out / "final_movie.json"
    if spec_file.is_file():
        spec = read_json(spec_file)
        bad = [k for k, ok in (spec.get("spec_ok") or {}).items() if not ok]
        print(f"    [spec] {spec.get('spec')}  audio={spec.get('audio_level')}")
        if bad:
            print(f"    [warn] 规格自检未过: {', '.join(bad)}")


def step_timeline(ctx: Ctx) -> None:
    argv = [
        sys.executable, REPO / "living-picturebook" / "scripts" / "book_timeline.py",
        "--plan", ctx.plan_path,
        "--out-dir", ctx.out_dir,
    ]
    if not ctx.dry_run:
        argv += ["--clips-dir", ctx.relay_out]
    run(ctx, "S5b 页时间码（book_timeline）", argv, cwd=REPO)
    for name in ("page_timeline.json", "page_timeline.md"):
        p = ctx.out_dir / name
        if p.is_file():
            print(f"    [out] {p}")


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    func: object
    costly: bool


STEPS = [
    Step("plan", "S3 分镜", step_plan, False),
    Step("duration", "S3c 时长对齐", step_duration, False),
    Step("bridge", "S3b 桥接帧", step_bridge, False),
    Step("enrich", "S3d 提示词增强", step_enrich, False),
    Step("preflight", "S4a 生成前检查", step_preflight, False),
    Step("generate", "S4b 提交生成", step_generate, True),
    Step("poll", "S4c 轮询下载", step_poll, True),
    Step("qc", "S4d 质检", step_qc, False),
    Step("narration", "S4e 旁白", step_narration, False),
    Step("concat", "S5 拼接", step_concat, False),
    Step("timeline", "S5b 页时间码", step_timeline, False),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="整本绘本一次生成：五个 skill 的确定性串联器")
    ap.add_argument("--pages-dir", type=Path, default=REPO / "pages", help="页目录（page_###/ 四件套）")
    ap.add_argument("--out-dir", type=Path, default=REPO / "book_out", help="工作目录（scene_plan / 时间码）")
    ap.add_argument("--plan", type=Path, default=None, help="scene_plan.json（默认 <out-dir>/scene_plan.json）")
    ap.add_argument("--relay-out", type=Path, default=None, help="relay 工作目录（默认 <out-dir>/relay）")
    ap.add_argument("--resolution", choices=["480p", "720p", "1080p"], default="1080p")
    ap.add_argument("--target-seconds", type=int, default=0,
                    help="把总时长对齐到官方区间（如 120）；0=不改，保持 plan 原值")
    ap.add_argument("--dry-run", action="store_true", help="只跑到生成 payload，不调 API、不花钱")
    ap.add_argument("--yes", action="store_true", help="超过预算守卫时确认执行")
    ap.add_argument("--keep-going", action="store_true", help="某步失败也继续（默认失败即停）")
    ap.add_argument("--from", dest="from_step", default=None, help="从哪一步开始（步骤 key）")
    ap.add_argument("--to", dest="to_step", default=None, help="到哪一步结束（含）")
    ap.add_argument("--poll-timeout-min", type=int, default=30, help="轮询总超时（分钟）")
    ap.add_argument("--scene-scripts", type=Path, default=None, help="picturebook-scene-director/scripts")
    ap.add_argument("--relay-root", type=Path, default=None, help="seedance-relay-producer 根目录")
    ap.add_argument("--book", default="", help="书目录（books/<id>）；不给就从页 manifest 的 book_id 反查")
    ap.add_argument("--narration", type=Path, default=None,
                    help="现成的旁白轨（mp3/wav）；concat 会铺进成片")
    ap.add_argument("--build-narration", action="store_true",
                    help="现场合成旁白（build_audio_plan -> build_narration，调 MiniMax TTS）")
    ap.add_argument("--list", action="store_true", help="列出步骤后退出")
    args = ap.parse_args(argv)

    if args.list:
        for i, s in enumerate(STEPS, 1):
            mark = "（花钱）" if s.costly else ""
            print(f"{i}. {s.key:<10} {s.title}{mark}")
        return 0

    pages_dir = args.pages_dir.expanduser().resolve()
    if not pages_dir.is_dir():
        raise SystemExit(f"[pipeline] 页目录不存在: {pages_dir}")
    if not any(p.is_dir() and (p / "clean_keyframe.png").is_file() for p in pages_dir.iterdir()):
        raise SystemExit(f"[pipeline] {pages_dir} 下没有 page_###/clean_keyframe.png，先跑 S1/S2。")
    gate_keyframe_text(pages_dir)

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(
        pages_dir=pages_dir,
        out_dir=out_dir,
        plan_path=(args.plan.expanduser().resolve() if args.plan else out_dir / "scene_plan.json"),
        relay_out=(args.relay_out.expanduser().resolve() if args.relay_out else out_dir / "relay"),
        resolution=args.resolution,
        dry_run=args.dry_run,
        yes=args.yes,
        keep_going=args.keep_going,
        poll_timeout_sec=max(60, args.poll_timeout_min * 60),
        scene_scripts=find_scene_scripts(args.scene_scripts),
        relay_root=find_relay_root(args.relay_root),
        target_seconds=args.target_seconds,
        book=args.book,
        narration=(args.narration.expanduser().resolve() if args.narration else None),
        build_narration=args.build_narration,
    )

    keys = [s.key for s in STEPS]
    start = keys.index(args.from_step) if args.from_step else 0
    end = keys.index(args.to_step) if args.to_step else len(STEPS) - 1
    if start > end:
        raise SystemExit("[pipeline] --from 在 --to 之后。")

    print(f"[pipeline] pages={ctx.pages_dir}")
    print(f"[pipeline] out={ctx.out_dir}  relay_out={ctx.relay_out}")
    print(f"[pipeline] resolution={ctx.resolution}  dry_run={ctx.dry_run}")
    print(f"[pipeline] director={ctx.scene_scripts}")

    if not ctx.dry_run and any(s.costly and keys.index(s.key) >= start for s in STEPS):
        print("\n[pipeline] 即将真实调用 Seedance（会产生费用）。5 秒内可 Ctrl+C 取消。")
        time.sleep(5)

    for step in STEPS[start:end + 1]:
        step.func(ctx)  # type: ignore[operator]

    print("\n[pipeline] 完成。")
    if ctx.dry_run:
        print("[pipeline] 这是 dry-run：没有任何视频生成。去掉 --dry-run 并设置 ARK_API_KEY 才会真跑。")
    else:
        for rel in ("relay/final_movie.mp4", "page_timeline.json"):
            p = ctx.out_dir / rel
            print(f"  - {rel}: {'OK' if p.is_file() else 'MISSING'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
