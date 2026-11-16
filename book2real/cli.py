"""Command line entry point: `python -m book2real <command>`.

  plan      show the shot list for a book without rendering
  run       plan -> render -> QC -> assemble a film
  ui        serve the player + dashboard for a runs/ directory
  doctor    environment check (ffmpeg, numpy, Pillow, fonts)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .config import Config


def _load_cfg(path: str | None) -> Config:
    return Config.load(path)


def cmd_plan(args) -> int:
    from .storyboard import plan

    meta, shots = plan(args.book, b_track=not args.no_b)
    print(f"book   : {meta['book_id']}  ({meta.get('title', '')})")
    print(f"pages  : {len(meta['pages'])}")
    print(f"shots  : {len(shots)}   hero={sum(1 for s in shots if s.hero)}")
    print(f"{'shot':<6}{'page':>5}{'track':<12}{'move':<11}{'dur':>6}  text")
    for s in shots:
        txt = (s.text_cues[0]["text"] if s.text_cues else "")[:26]
        print(f"{s.shot_id:<6}{s.page:>5}{s.track:<12}{s.camera.move:<11}"
              f"{s.duration:>6.1f}  {txt}")
    total = sum(s.duration for s in shots) + args.title_seconds + 2.0
    print(f"\nplanned film: {total:.1f}s   (title {args.title_seconds}s + end 2s)")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"book": meta, "shots": [s.to_dict() for s in shots]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


def cmd_run(args) -> int:
    from .pipeline import Runner

    cfg = _load_cfg(args.config)
    if args.fps:
        cfg.film.fps = args.fps
    cfg.runtime.budget_s = args.budget
    t0 = time.perf_counter()
    runner = Runner(cfg, out_root=args.out, jobs=args.jobs,
                    b_track=not args.no_b, force=args.force)
    manifest, run_dir = runner.run(args.book)
    print(f"\nrun dir : {run_dir}")
    print(f"film    : {run_dir / 'film.mp4'}  ({manifest.film.get('duration')}s)")
    print(f"stats   : {json.dumps(manifest.stats, ensure_ascii=False)}")
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{args.port}/")
    return 0


def cmd_ui(args) -> int:
    from .webapp import serve

    return serve(args.runs, port=args.port, open_browser=args.open)


def cmd_reader(args) -> int:
    from .reader_app import serve

    return serve(args.books, port=args.port, open_browser=args.open,
                 host=getattr(args, "host", "127.0.0.1"))


def cmd_import_pdf(args) -> int:
    from .ingest import import_pdf

    report = import_pdf(args.pdf, book_id=args.id, out_root=args.out,
                        dpi=args.dpi, force=args.force)
    print(f"book   : {report['book_id']}  ({report['title']})")
    print(f"dir    : {report['dir']}")
    print(f"pages  : {report['pages']}  rendered {report['rendered']}"
          f"  skipped {report['skipped']}  text layer {report['text_pages']}")
    print("next   : python -m book2real analyze --book " + report["dir"])
    return 0


def cmd_cloud(args) -> int:
    from . import cloud

    cloud.load_env()
    if args.check:
        print(f"{'provider':<10}{'ready':<7}{'model':<42}base")
        for r in cloud.provider_status():
            print(f"{r['provider']:<10}{'yes' if r['ready'] else 'no':<7}"
                  f"{r['model']:<42}{r['base']}")
        print(f"\n已用 元{cloud.spent_cny():.2f} / 上限 元{cloud._cfg()['budget']:.2f}"
              f"  账本 runs/cloud/ledger.jsonl")
        missing = [r["env"] for r in cloud.provider_status() if not r["ready"]]
        if missing:
            print(f"缺 key：{', '.join(sorted(set(missing)))}（写进 .env，见 docs/API_KEYS.md）")
        return 0

    if args.llm:
        out = cloud.deepseek([{"role": "user", "content": args.llm}],
                             dry_run=args.dry_run)
        if not args.dry_run:
            print(out["text"])
        print(f"[cloud] deepseek {out['model']}  元{out['cny']}", file=sys.stderr)
        return 0

    if args.image:
        out = cloud.seedream(args.image, args.out or ".tmp/cloud/image.png",
                             size=args.size, dry_run=args.dry_run)
        print(out.get("path") or "(dry run)")
        print(f"[cloud] seedream {out['model']}  元{out['cny']}", file=sys.stderr)
        return 0

    if args.video:
        out = cloud.seedance(args.video, args.out or ".tmp/cloud/video.mp4",
                             image=args.image_path, seconds=args.seconds,
                             resolution=args.resolution, ratio=args.ratio,
                             dry_run=args.dry_run)
        print(out.get("path") or "(dry run)")
        print(f"[cloud] seedance {out['model']}  元{out['cny']}", file=sys.stderr)
        return 0

    print("用法：cloud --check | --llm TEXT | --image PROMPT --out FILE | "
          "--video PROMPT --out FILE [--image-path PAGE]")
    return 2


def cmd_story(args) -> int:
    from .creative import apply_scenes, story

    data = story(args.book, force=args.force, model=args.model)
    print(f"story -> {Path(args.book) / 'reader' / 'story' / 'story.json'}")
    print(f"pages {len(data.get('pages') or [])}  model {data.get('model')}"
          f"  cost CNY {data.get('cny')}")
    if args.apply:
        n = apply_scenes(args.book)
        print(f"applied scene to curation.json: {n} page(s); re-run analyze to publish")
    else:
        print("tip: pass --apply to copy the scenes into curation.json")
    return 0


def cmd_visuals(args) -> int:
    from .creative import visuals

    rows = visuals(args.book, force=args.force, limit=args.limit, model=args.model)
    print(f"visuals: {len(rows)} generated")
    for r in rows:
        print(f"  {r['page']}  {r['path']}  CNY {r.get('cny')}")
    if rows:
        print("next: python -m book2real analyze --book " + args.book)
    return 0


def cmd_clip(args) -> int:
    from .creative import clip

    out = args.out or f"runs/{Path(args.book).name}/clips/{args.page}.mp4"
    r = clip(args.book, args.page, args.prompt, out, seconds=args.seconds,
             model=args.model, image=not args.text_only)
    print(f"clip -> {r.get('path')}  model {r.get('model')}  CNY {r.get('cny')}")
    print("note: 仅用于演示/试片；正式成片仍走单趟 film 路径（Q43）")
    return 0


def cmd_analyze(args) -> int:
    from .storybook import build

    data = build(args.book, force=args.force)
    print(f"reader package -> {Path(args.book) / 'reader' / 'book.json'}")
    print(f"pages {data['stats']['pages']}  ocr blocks {data['stats']['ocr_blocks']}")
    return 0


def cmd_cast(args) -> int:
    from .cast.pipeline import run

    report = run(args.book, force=args.force, max_rigs=args.max_rigs,
                 models=getattr(args, "models", False),
                 on_progress=lambda m: print(f"  {m}"))
    stages = report["stages"]
    print(f"\ncast -> {Path(args.book) / 'reader' / 'characters' / 'characters.json'}"
          f"  ({report['seconds']}s)")
    print(f"pages    : {stages['PageScout']['pages']}"
          f"  entities {stages['PageScout']['entities']}"
          f"  degraded {stages['PageScout']['degraded']}")
    print(f"cast     : {stages['CastClerk']['characters']} characters"
          f"  ({stages['CastClerk']['named_entities']} named,"
          f" {stages['CastClerk']['anonymous_entities']} anonymous)")
    for cid, info in stages["Rigger"].items():
        print(f"  rig    : {cid:<10} {info['kind']:<12} parts={info['parts']}"
              f" conf={info['confidence']:.2f}")
    print(f"motion   : {stages['MotionDirector']['plans']} plans,"
          f" {stages['MotionDirector']['beats']} beats")
    cand = stages.get("RiggerCandidates", {})
    if cand:
        print(f"candidates: {cand['accepted']}/{cand['considered']} unnamed figures rigged")
    crit = stages["Critic"]
    print(f"critic   : {crit['characters_accepted']}/{crit['characters_judged']} accepted,"
          f" text coverage {crit['text_coverage']:.0%}")
    if crit["missing"]:
        print(f"  missing: {', '.join(crit['missing'])}")
    return 0


def cmd_models(args) -> int:
    from .models import fetch, status

    if args.fetch:
        return fetch(args.only or None)
    rows = status()
    for r in rows:
        mark = "ok  " if r["installed"] else "miss"
        print(f"[{mark}] {r['key']:<8} {r['repo']}")
        print(f"        {r['desc']}")
    missing = [r["key"] for r in rows if not r["installed"]]
    if missing:
        print(f"\nrun `python -m book2real models --fetch` to download: {', '.join(missing)}")
    return 0


def cmd_layers(args) -> int:
    from .layering import run as build_layers

    index = build_layers(args.book, use_models=not args.no_models, limit=args.limit,
                         on_progress=lambda m: print(f"  {m}"))
    print(f"\nlayers -> {Path(args.book) / 'reader' / 'layers' / 'index.json'}"
          f"  ({index['seconds']}s)")
    print(f"pages  : {index['stats']['pages']}   inpaint {index['stats']['inpaint']}")
    return 0


def cmd_castcheck(args) -> int:
    import json as _json

    from .cast.castcheck import run as check

    out = check(args.book)
    print(f"characters: {out['characters']}   duplicates: {len(out['duplicates'])}")
    for d in out["duplicates"]:
        print(f"  {d['a']} == {d['b']}  similarity {d['similarity']}"
              f"  (pages {d.get('page_a')} / {d.get('page_b')})")
        if d.get("hint"):
            print(f"    hint: {d['hint']}")
    if args.json:
        open(args.json, "w", encoding="utf-8").write(_json.dumps(out, ensure_ascii=False, indent=2))
        print(f"wrote {args.json}")
    return 0


def cmd_agent(args) -> int:
    from .agent import main as agent_main

    rest = list(args.args or [])
    return agent_main(rest)


def cmd_screens(args) -> int:
    from .screens import apply_plan, plan_book, report

    plan = plan_book(args.book)
    print(report(plan))
    if args.apply:
        n = apply_plan(args.book, plan)
        print(f"\nwrote split_plan for {n} page(s) into curation.json")
    else:
        print("\n(dry run: pass --apply to write these decisions into curation.json)")
    return 0


def cmd_sync(args) -> int:
    from .syncmedia import sync

    out = sync(args.book)
    print(f"layers synced: {out.get('pages')} pages")
    for pid, name in (out.get("attached") or {}).items():
        print(f"  {pid} -> {name}")
    if out.get("dropped"):
        print(f"  dropped missing loops: {', '.join(out['dropped'])}")
    return 0


def cmd_words(args) -> int:
    from .words import build

    out = build(args.book, use_llm=args.llm,
                on_progress=lambda m: print(f"  {m}"))
    print(f"words -> {Path(args.book) / 'reader' / 'words'}")
    print(f"pages {out['pages']}  rule {out['rule']}  llm {out['llm']}")
    if not args.llm:
        print("tip: fill LLM_API_* in .env and pass --llm for kid-friendly meanings")
    return 0


def cmd_narrate(args) -> int:
    from .narrate import build

    out = build(args.book, synth=not args.timings_only,
                on_progress=lambda m: print(f"  {m}"))
    print(f"narration -> {Path(args.book) / 'reader' / 'narration'}")
    print(f"pages {out['pages']}  synth {out['synth']}  cached {out['cached']}"
          f"  uniform {out['uniform']}")
    return 0


def cmd_film(args) -> int:
    from .storyfilm import render

    meta = render(args.book)
    print(f"film -> runs/{meta['path']}  {meta['duration']}s  narrated={meta['narrated']}")
    return 0


def cmd_evaluate(args) -> int:
    from . import evaluate as ev

    try:
        report = ev.evaluate(
            book=args.book,
            audio=args.audio,
            script=args.script,
            limit=args.limit,
            asr=args.asr,
            labels=args.labels,
        )
        paths = ev.write_report(report)
        if args.json:
            out = Path(args.json)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            print(f"[evaluate] json -> {out}")
    except ev.EvaluateError as exc:
        print(f"[evaluate] {exc}", file=sys.stderr)
        return 2

    m = report["metrics"]
    print(f"[evaluate] json -> {paths['json']}")
    print(f"[evaluate] md   -> {paths['md']}")
    if m["transport_skipped"]:
        print(f"[evaluate] transport 行 {m['transport_skipped']} 条不计 Query")
    acc = report["accuracy"]
    if acc is None:
        if report.get("labels", {}).get("provided"):
            acc_text = "null（标注与可评测条目无交集）"
        else:
            acc_text = "null（未提供 --labels）"
    else:
        acc_text = str(acc)
    print(f"[evaluate] accuracy {acc_text}  coverage {m['coverage']}")
    print(f"结果：{m['total']} 条，成功 {m['success']}，跳过 {m['skipped']}")
    return 0


def cmd_doctor(args) -> int:
    import importlib

    ok = True
    for mod in ("numpy", "PIL"):
        try:
            m = importlib.import_module(mod)
            print(f"[ok]   {mod} {getattr(m, '__version__', '?')}")
        except Exception as exc:                     # noqa: BLE001
            ok = False
            print(f"[FAIL] {mod}: {exc}")
    try:
        from .util import ffmpeg_path
        import subprocess

        p = ffmpeg_path()
        v = subprocess.run([p, "-version"], capture_output=True, text=True).stdout.split("\n")[0]
        print(f"[ok]   ffmpeg {v.split(' ')[2] if len(v.split(' ')) > 2 else ''}  ({p})")
    except Exception as exc:                         # noqa: BLE001
        ok = False
        print(f"[FAIL] ffmpeg: {exc}")
    fonts = [f for f in Path("C:/Windows/Fonts").glob("msyh*")] if Path("C:/Windows/Fonts").exists() else []
    print(f"[{'ok' if fonts else 'warn'}]   CJK font: {fonts[0].name if fonts else 'none found'}")
    print(f"[ok]   book2real {__version__}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="book2real", description="Picture book -> story film")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="show the shot list")
    sp.add_argument("--book", required=True)
    sp.add_argument("--no-b", action="store_true", help="disable the B track")
    sp.add_argument("--title-seconds", type=float, default=3.0)
    sp.add_argument("--json")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("run", help="render a film")
    sp.add_argument("--book", required=True)
    sp.add_argument("--out", default="runs")
    sp.add_argument("--config")
    sp.add_argument("--jobs", type=int, default=2)
    sp.add_argument("--fps", type=int)
    sp.add_argument("--budget", type=float, default=900.0, help="wall-clock budget in seconds")
    sp.add_argument("--no-b", action="store_true")
    sp.add_argument("--force", action="store_true", help="ignore the shot cache")
    sp.add_argument("--open", action="store_true", help="open the player when done")
    sp.add_argument("--port", type=int, default=8765)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("ui", help="serve the player / dashboard")
    sp.add_argument("--runs", default="runs")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--open", action="store_true")
    sp.set_defaults(func=cmd_ui)

    sp = sub.add_parser("reader", help="serve the interactive picture-book reader")
    sp.add_argument("--books", default="books")
    sp.add_argument("--port", type=int, default=8770)
    sp.add_argument("--host", default="127.0.0.1",
                    help="bind address; use 0.0.0.0 to reach it from a tablet on the LAN")
    sp.add_argument("--open", action="store_true")
    sp.set_defaults(func=cmd_reader)

    sp = sub.add_parser("cloud", help="DeepSeek / Seedream / Seedance probes and calls")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="who is configured, and spend so far")
    g.add_argument("--llm", metavar="TEXT")
    g.add_argument("--image", metavar="PROMPT")
    g.add_argument("--video", metavar="PROMPT")
    sp.add_argument("--image-path", help="first frame for Seedance image-to-video")
    sp.add_argument("--out")
    sp.add_argument("--size", default="1024x1024")
    sp.add_argument("--seconds", type=int, default=5)
    sp.add_argument("--resolution", default="720p")
    sp.add_argument("--ratio", default="16:9")
    sp.add_argument("--dry-run", action="store_true", help="price the call, do not send it")
    sp.set_defaults(func=cmd_cloud)

    sp = sub.add_parser("import-pdf", help="render an official picture-book PDF into books/<id>")
    sp.add_argument("pdf")
    sp.add_argument("--id", help="book id / directory name (default: PDF file name)")
    sp.add_argument("--out", default="books")
    sp.add_argument("--dpi", type=int, default=170)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_import_pdf)

    sp = sub.add_parser("story", help="DeepSeek writes per-page scenes and questions")
    sp.add_argument("--book", required=True)
    sp.add_argument("--model")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--apply", action="store_true", help="copy scenes into curation.json")
    sp.set_defaults(func=cmd_story)

    sp = sub.add_parser("visuals", help="Seedream fills split-screen art marked 'regenerate'")
    sp.add_argument("--book", required=True)
    sp.add_argument("--model")
    sp.add_argument("--limit", type=int, default=0)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_visuals)

    sp = sub.add_parser("clip", help="one Seedance probe clip for a page (not the film)")
    sp.add_argument("--book", required=True)
    sp.add_argument("--page", required=True)
    sp.add_argument("--prompt", required=True)
    sp.add_argument("--out")
    sp.add_argument("--seconds", type=int, default=5)
    sp.add_argument("--model")
    sp.add_argument("--text-only", action="store_true", help="ignore the page image")
    sp.set_defaults(func=cmd_clip)

    sp = sub.add_parser("analyze", help="OCR a book and build its reader package")
    sp.add_argument("--book", required=True)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("cast", help="run the cast agents (page scout -> rig -> motion)")
    sp.add_argument("--book", required=True)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--max-rigs", type=int, default=3,
                    help="how many characters get a paper-puppet rig")
    sp.add_argument("--models", action="store_true",
                    help="let the rigger repair cut edges with the local inpainting model")
    sp.set_defaults(func=cmd_cast)

    sp = sub.add_parser("models", help="report or download the optional model weights")
    sp.add_argument("--fetch", action="store_true")
    sp.add_argument("--only", nargs="*", help="depth inpaint sam")
    sp.set_defaults(func=cmd_models)

    sp = sub.add_parser("layers", help="build 2.5D layers (background + characters + depth)")
    sp.add_argument("--book", required=True)
    sp.add_argument("--no-models", action="store_true",
                    help="skip depth/inpainting (pure numpy, much faster)")
    sp.add_argument("--limit", type=int, default=0, help="stop after N pages")
    sp.set_defaults(func=cmd_layers)

    sp = sub.add_parser("castcheck", help="report characters that share a portrait")
    sp.add_argument("--book", required=True)
    sp.add_argument("--json")
    sp.set_defaults(func=cmd_castcheck)

    sp = sub.add_parser("agent", help="steps an outside agent can call (rig, motion, leaf)")
    sp.add_argument("args", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_agent)

    sp = sub.add_parser("sync", help="re-attach generated loops after a layers rebuild")
    sp.add_argument("--book", required=True)
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("screens", help="decide screens per page: how many, and what each shows")
    sp.add_argument("--book", required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_screens)

    sp = sub.add_parser("words", help="build a per-page word list with meanings")
    sp.add_argument("--book", required=True)
    sp.add_argument("--llm", action="store_true", help="ask the configured LLM")
    sp.set_defaults(func=cmd_words)

    sp = sub.add_parser("narrate", help="per-character narration timings for the reader")
    sp.add_argument("--book", required=True)
    sp.add_argument("--timings-only", action="store_true")
    sp.set_defaults(func=cmd_narrate)

    sp = sub.add_parser("film", help="render the interactive book as a 16:9 film")
    sp.add_argument("--book", required=True)
    sp.set_defaults(func=cmd_film)

    sp = sub.add_parser("evaluate", help="offline ASR/retrieval/QA report for a book")
    sp.add_argument("--book", required=True)
    sp.add_argument("--audio", required=True, help="official per-book audio directory")
    sp.add_argument("--script", help="recording script override (default: official by id)")
    sp.add_argument("--limit", type=int, help="evaluate only the first N queries")
    sp.add_argument("--asr", choices=("auto", "none"), default="auto")
    sp.add_argument("--labels", help="JSON {n: page_id} to compute top-1 accuracy")
    sp.add_argument("--json", help="write a copy of the report JSON here")
    sp.set_defaults(func=cmd_evaluate)

    sp = sub.add_parser("doctor", help="environment check")
    sp.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    from . import cloud

    cloud.load_env()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except cloud.CloudError as exc:
        print(f"[cloud] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
