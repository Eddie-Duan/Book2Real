# -*- coding: utf-8 -*-
"""CLI for picturebook-one-take."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from assemble import assemble  # noqa: E402
from assets import build_assets  # noqa: E402
from bible import build_bible  # noqa: E402
from common import (ARK_KEY, DEEPSEEK_KEY, MINIMAX_KEY, ROOT, ffprobe, jread,  # noqa: E402
                    jwrite, load_env)
from contract import lint_all, validate_script  # noqa: E402
from cover import add_cover_intro, shift_timeline  # noqa: E402
from generate import run_chain  # noqa: E402
from planner import plan as plan_fn  # noqa: E402
from vision import analyze_book, merge_into_bible  # noqa: E402
from prompting import write_prompts  # noqa: E402


def _paths(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bible = jread(out / "bible.json") if (out / "bible.json").exists() else None
    script = jread(out / "shot_script.json") if (out / "shot_script.json").exists() else None
    return out, bible, script


def cmd_doctor(args):
    env = load_env()
    checks = [
        ("ffmpeg", __import__("shutil").which("ffmpeg") is not None),
        ("ffprobe", __import__("shutil").which("ffprobe") is not None),
        ("ARK_API_KEY", bool(ARK_KEY)),
        ("DEEPSEEK_API_KEY", bool(DEEPSEEK_KEY)),
        ("MINIMAX_API_KEY", bool(MINIMAX_KEY)),
        ("yunet.onnx", (ROOT / ".tmp" / "models" / "yunet.onnx").exists()),
    ]
    try:
        import cv2, numpy, torch, torchvision  # noqa: F401
        checks.append(("torchvision", True))
    except Exception:  # noqa: BLE001
        checks.append(("torchvision", False))
    try:
        import rapidocr_onnxruntime  # noqa: F401
        checks.append(("rapidocr", True))
    except Exception:  # noqa: BLE001
        checks.append(("rapidocr", False))
    for name, ok in checks:
        print(f"{'OK ' if ok else 'MISS'} {name}")
    print("models:", env.get("DEEPSEEK_MODEL"), "| seedream 5.5 | seedance 2.5 flash")
    return 0 if all(ok for _, ok in checks) else 1



def cmd_vision(args):
    out, bible, _ = _paths(args)
    bible = bible or build_bible(args.book)
    vision = analyze_book(bible, out, args.max_pages)
    merged = merge_into_bible(bible, vision)
    jwrite(out / "bible.json", merged)
    print(f"vision: {len(vision['characters'])} chars / {len(vision['props'])} props / "
          f"{len(vision['locations'])} locations -> {out/'vision_bible.json'}")
    return 0


def cmd_bible(args):
    bible = build_bible(args.book)
    jwrite(Path(args.out) / "bible.json", bible)
    print(f"bible: {len(bible['characters'])} chars / {len(bible['pages'])} pages -> {args.out}")
    return 0


def cmd_plan(args):
    out, bible, _ = _paths(args)
    bible = bible or build_bible(args.book)
    jwrite(out / "bible.json", bible)
    script = plan_fn(args.book, bible, out, args.target_seconds, True, args.script,
                     getattr(args, "style", None),
                     getattr(args, "from_example", False),
                     getattr(args, "max_attempts", 2))
    result = validate_script(script, bible)
    print(f"plan: {len(script['chunks'])} chunks / {result['total_seconds']}s / "
          f"{len(result['pages_covered'])} pages")
    return 0


def cmd_preflight(args):
    out, bible, script = _paths(args)
    if not bible or not script:
        print("need bible.json + shot_script.json"); return 1
    result = validate_script(script, bible)
    est = sum(float(c["duration"]) for c in script["chunks"]) * 1.0
    est_assets = 3 * 0.12
    ok = result["passed"]
    jwrite(out / "preflight.json", {"script": result, "est_video_cny": est,
                                    "est_assets_cny": est_assets,
                                    "est_total_cny": round(est + est_assets, 2)})
    print(f"preflight {'PASS' if ok else 'FAIL'}: {result['total_seconds']}s, "
          f"est video CNY {est:.1f} + assets CNY {est_assets:.2f}")
    for f in result["failures"]:
        print("  -", f["code"], f["message"])
    return 0 if ok else 1


def _prompt_maps(script, bible, out):
    manifest = jread(out / "assets" / "assets.json")
    maps, locs, pmaps = {}, {}, {}
    props = {p["id"]: p for p in bible.get("props") or []}
    for c in script["chunks"]:
        m, pm = {}, {}
        n = 0
        # first-frame chunks do not send character reference images
        if not c.get("first_frame"):
            for cid in c.get("cast") or []:
                if (out / f"assets/head_{cid}.png").exists():
                    n += 1
                    m[cid] = n
            seen_refs = set()
            pids = list(c.get("props") or []) or [p["id"] for p in (bible.get("props") or [])
                    if set(c.get("pages") or []).intersection(p.get("pages") or [])]
            for pid in pids:
                ref = (props.get(pid) or {}).get("ref")
                refp = (out / ref) if ref else None
                if refp is not None and refp.exists() and str(refp) not in seen_refs:
                    pm[pid] = n + len(pm) + 1
                    seen_refs.add(str(refp))
        maps[c["id"]] = m
        pmaps[c["id"]] = pm
        if c.get("location_ref"):
            locs[c["id"]] = n + len(pm) + 1
    return manifest, maps, locs, pmaps


def cmd_prompts(args):
    out, bible, script = _paths(args)
    if not bible or not script:
        print("need bible.json + shot_script.json"); return 1
    manifest, maps, locs, pmaps = _prompt_maps(script, bible, out)
    # replace logical first_frame with the real plate path for prompt readability
    for c in script["chunks"]:
        ff = c.get("first_frame")
        if ff and not ff.startswith("assets/"):
            entry = manifest.get(f"plate_{ff}") or {}
            if entry.get("plate"):
                c["first_frame"] = entry["plate"]
    write_prompts(script, bible, out, maps, locs, pmaps)
    lint = lint_all(out / "prompts", script, bible)
    jwrite(out / "prompt_lint.json", lint)
    print(f"prompts: {len(script['chunks'])} written; lint {'PASS' if lint['passed'] else 'FAIL'}")
    if not lint["passed"]:
        for cid, res in lint["chunks"].items():
            for f in res.get("failures") or []:
                print("  -", cid, f["code"], f["message"])
    return 0 if lint["passed"] else 1


def cmd_assets(args):
    out, bible, _ = _paths(args)
    if not bible:
        print("need bible.json"); return 1
    which = [x.strip() for x in args.which.split(",") if x.strip()]
    manifest = build_assets(bible, out, which)
    print(f"assets: {len(manifest)} entries -> {out/'assets'}")
    return 0


def cmd_generate(args):
    out, bible, script = _paths(args)
    if not bible or not script:
        print("need bible.json + shot_script.json"); return 1
    result = run_chain(bible, script, out, args.resolution, args.max_cny,
                       args.start, args.end, args.force, args.attempts)
    print(f"generate: {len(result['reports'])} chunks, est CNY {result['est_cny']}")
    return 0 if all(r["verdict"] == "PASS" for r in result["reports"]) else 1


def cmd_cover(args):
    out = Path(args.out)
    movie = Path(args.movie or (out / "final_movie.mp4"))
    meta = add_cover_intro(ROOT / args.book, movie, out / "final_movie_cover.mp4",
                           args.seconds, getattr(args, "voice", None))
    tl = out / "timeline.json"
    if tl.exists():
        out_tl = out / "timeline_cover.json"
        out_tl.write_text(json.dumps(shift_timeline(jread(tl), meta["cover_seconds"]),
                                     ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        meta["timeline"] = str(out_tl)
    print("cover:", json.dumps(meta, ensure_ascii=False))
    return 0


def cmd_assemble(args):
    meta = assemble(args.out)
    print(f"final: {meta['file']} {meta['duration']}s {meta['width']}x{meta['height']} @{meta['fps']}")
    return 0


def cmd_verify(args):
    delivery = bool(getattr(args, "delivery", False))
    out = Path(args.out)
    meta = jread(out / "final_movie.json")
    script = jread(out / "shot_script.json")
    movie = Path(getattr(args, "movie", "") or meta["file"])
    info = ffprobe(movie)
    v = [s for s in info["streams"] if s.get("codec_type") == "video"][0]
    a = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    dur = float(info["format"]["duration"])
    failures = []
    allowed = ((1280, 720),) if delivery else ((854, 480), (1280, 720))
    if (v["width"], v["height"]) not in allowed:
        failures.append(f"final resolution {v['width']}x{v['height']} (delivery={delivery})")
    if v["r_frame_rate"] != "24/1": failures.append("final fps")
    if not a: failures.append("final audio")
    if not (100 <= dur <= 200.5): failures.append(f"final duration {dur:.1f}")
    reps = []
    for c in script["chunks"]:
        p = out / f"{c['id']}_gate.json"
        if not p.exists():
            failures.append(f"{c['id']} no gate report"); continue
        rep = jread(p); reps.append(rep)
        if rep.get("verdict") != "PASS":
            failures.append(f"{c['id']} gate FAIL")
    summary = {"verdict": "PASS" if not failures else "FAIL", "duration": dur,
               "width": v["width"], "height": v["height"], "fps": v["r_frame_rate"],
               "audio": bool(a), "chunks": len(reps),
               "gate_failures": [f for r in reps for f in r.get("failures") or []],
               "failures": failures}
    jwrite(out / "verify.json", summary)
    print(f"verify {'PASS' if not failures else 'FAIL'} ({ 'DELIVERY' if delivery else 'TRAIN' }): "
          f"{dur:.1f}s {v['width']}x{v['height']} @{v['r_frame_rate']} audio={bool(a)}")
    for f in failures:
        print("  -", f)
    return 0 if not failures else 1


def cmd_run(args):
    if not args.yes:
        print("run spends money; pass --yes"); return 2
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    bible = build_bible(args.book); jwrite(out / "bible.json", bible)
    script = plan_fn(args.book, bible, out, args.target_seconds, True, args.script,
                     getattr(args, "style", None),
                     getattr(args, "from_example", False),
                     getattr(args, "max_attempts", 2))
    if validate_script(script, bible)["passed"] is False:
        return 1
    if args.assets:
        build_assets(bible, out, [x.strip() for x in args.assets.split(",") if x.strip()])
    if args.prompts:
        # need assets first for head indices
        rc = cmd_prompts(argparse.Namespace(out=str(out)))
        if rc != 0:
            return rc
    result = run_chain(bible, script, out, args.resolution, args.max_cny,
                       1, None, False, args.attempts)
    if not all(r["verdict"] == "PASS" for r in result["reports"]):
        return 1
    assemble(out)
    final = out / "final_movie.mp4"
    if getattr(args, "cover", True):
        try:
            cover_out = out / "final_movie_cover.mp4"
            meta = add_cover_intro(Path(args.book), final, cover_out,
                                   getattr(args, "cover_seconds", 2.0), getattr(args, "voice", None))
            tl = out / "timeline.json"
            if tl.exists():
                (out / "timeline_cover.json").write_text(json.dumps(
                    shift_timeline(jread(tl), meta["cover_seconds"]), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
            final = cover_out
        except Exception as exc:  # keep the one-take film if TTS/ffmpeg fails
            print(f"cover skipped: {exc}")
    return cmd_verify(argparse.Namespace(out=str(out), movie=str(final), delivery=False))


def main(argv=None):
    ap = argparse.ArgumentParser("picturebook-one-take")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor"); p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("bible"); p.add_argument("--book", required=True); p.add_argument("--out", required=True); p.set_defaults(fn=cmd_bible)
    p = sub.add_parser("vision"); p.add_argument("--book", required=True); p.add_argument("--out", required=True)
    p.add_argument("--max-pages", type=int, default=8); p.set_defaults(fn=cmd_vision)
    p = sub.add_parser("plan"); p.add_argument("--book", required=True); p.add_argument("--out", required=True)
    p.add_argument("--target-seconds", type=int, default=150); p.add_argument("--llm", action="store_true")
    p.add_argument("--script", default=None)
    p.add_argument("--style", default=None, help="books/<id>/style_card.json (skill4 叙述声音)")
    p.add_argument("--from-example", action="store_true", help="用预写示例分镜（回归基线，不调 LLM）")
    p.add_argument("--max-attempts", type=int, default=2, help="LLM 分镜最多尝试次数（含首次）")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("preflight"); p.add_argument("--out", required=True); p.set_defaults(fn=cmd_preflight)
    p = sub.add_parser("assets"); p.add_argument("--book", required=True); p.add_argument("--out", required=True)
    p.add_argument("--which", default="room,meadow,birthday"); p.set_defaults(fn=cmd_assets)
    p = sub.add_parser("prompts"); p.add_argument("--out", required=True); p.set_defaults(fn=cmd_prompts)
    p = sub.add_parser("generate"); p.add_argument("--out", required=True)
    p.add_argument("--resolution", default="480p"); p.add_argument("--max-cny", type=float, default=200.0)
    p.add_argument("--start", type=int, default=1); p.add_argument("--end", type=int, default=None)
    p.add_argument("--force", action="store_true"); p.add_argument("--attempts", type=int, default=1)
    p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("cover"); p.add_argument("--book", required=True); p.add_argument("--out", required=True)
    p.add_argument("--movie", default=None); p.add_argument("--seconds", type=float, default=2.0); p.add_argument("--voice", default=None); p.set_defaults(fn=cmd_cover)
    p = sub.add_parser("assemble"); p.add_argument("--out", required=True); p.set_defaults(fn=cmd_assemble)
    p = sub.add_parser("verify"); p.add_argument("--out", required=True); p.add_argument("--movie", default=None); p.add_argument("--delivery", action="store_true"); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("run"); p.add_argument("--book", required=True); p.add_argument("--out", required=True)
    p.add_argument("--target-seconds", type=int, default=150); p.add_argument("--llm", action="store_true")
    p.add_argument("--script", default=None)
    p.add_argument("--style", default=None, help="books/<id>/style_card.json (skill4 叙述声音)")
    p.add_argument("--from-example", action="store_true", help="用预写示例分镜（回归基线，不调 LLM）")
    p.add_argument("--max-attempts", type=int, default=2, help="LLM 分镜最多尝试次数（含首次）")
    p.add_argument("--assets", default="room,meadow,birthday")
    p.add_argument("--prompts", action="store_true"); p.add_argument("--resolution", default="480p")
    p.add_argument("--max-cny", type=float, default=200.0); p.add_argument("--attempts", type=int, default=1)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--no-cover", dest="cover", action="store_false", help="不生成 2s 封面+书名朗读")
    p.add_argument("--cover-seconds", type=float, default=2.0)
    p.set_defaults(fn=cmd_run, cover=True)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())