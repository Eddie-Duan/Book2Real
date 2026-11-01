"""Command line entry point: `python -m book2real <command>`.

  plan      show the shot list for a book without rendering
  run       plan -> render -> QC -> assemble a film
  ui        serve the player + dashboard for a runs/ directory
  sample    generate a synthetic demo book
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


def cmd_sample(args) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from samples.make_book import build

    out = build(args.out, pages=args.pages)
    print(f"sample book -> {out}")
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

    sp = sub.add_parser("sample", help="generate the demo book")
    sp.add_argument("--out", default="books")
    sp.add_argument("--pages", type=int, default=8)
    sp.set_defaults(func=cmd_sample)

    sp = sub.add_parser("doctor", help="environment check")
    sp.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
