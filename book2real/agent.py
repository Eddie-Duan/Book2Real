"""`python -m book2real agent <verb>` — the steps an outside agent needs.

Each verb is one decision, already made by a person or a planning model, turned
into files with a documented contract. They never browse the book, never write
prose, and never need this conversation: that is what makes them usable from a
different environment.

    part       point-prompted cut bounded by the ink line (no rectangle)
    probe      operator segmentation -> a decision table of regions
    inspect    SAM proposal for a point/box, as region ids
    cut        cut by region spec (edges, not rectangles)
    review     machine review of a cut piece
    rig-gap    cut a round region (a missing limb, a head) as a rigid piece
    rig-arm    cut a rigid limb by rectangle, optionally with sampled wave poses
    motion     beats JSON -> looping GIF / MP4 (rigid parts + sampled poses)
    plan       a small default plan for one page (sanity template)
    leaf       split a page into a flat background + several figure layers

`python -m book2real agent --help` lists them.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .util import ensure_dir, imread, read_json, write_json


def cmd_part(args) -> int:
    """Point-prompted cut: cells bounded by the ink line, no rectangle."""
    from .cast import partkit, segkit

    art = imread(Path(args.src))
    pts = [tuple(float(v) for v in a.split(",")) for a in args.at]
    rgba, meta = partkit.build(art, pts, tol=args.tol)
    out = ensure_dir(Path(args.to))
    png = out / f"{args.name}.png"
    Image.fromarray(rgba, "RGBA").save(png)
    pivot = partkit.end_pivot(rgba)
    meta["pivot"] = pivot
    meta["kind"] = "one_arm"
    meta["label"] = args.label
    write_json(out / f"{args.name}.json",
               {"kind": "one_arm", "art": args.src, "art_w": int(art.shape[1]),
                "art_h": int(art.shape[0]),
                "parts": [{"name": args.name, "image": str(png), "pivot": pivot,
                           "amp": args.amp}]})
    write_json(out / f"{args.name}_meta.json", meta)
    s = segkit.score(rgba, meta, art)
    print(json.dumps({"piece": str(png), "pivot": pivot, "review": s}, ensure_ascii=False))
    return 0 if s["accept"] else 2


def cmd_seg(args) -> int:
    from .cast import segkit

    painter = {"probe": segkit.cmd_probe, "inspect": segkit.cmd_inspect,
               "cut": segkit.cmd_cut, "review": segkit.cmd_review}.get(args.cmd)
    if painter is None:
        raise KeyError(args.cmd)
    return painter(args)


def cmd_rig(args) -> int:
    from .cast.agent_tools import cut_arm, cut_gap, _border_key

    art = imread(Path(args.src))
    out = ensure_dir(Path(args.out))
    if args.cmd == "rig-gap":
        pivot = None
        if args.pivot:
            p = [float(v) for v in args.pivot.split(",")]
            pivot = (p[0], p[1])
        part = cut_gap(art, args.cx, args.cy, args.r, pivot=pivot, name=args.name, out_dir=out)
        kind = "head_nod"
    else:
        bbox = tuple(float(v) for v in args.bbox.split(","))
        part = cut_arm(art, bbox, name=args.name, out_dir=out, step=args.step)
        kind = "one_arm"
    result = {"kind": kind, "art": str(args.src), "art_w": int(art.shape[1]),
              "art_h": int(art.shape[0]), "key": [int(v) for v in _border_key(art)],
              "parts": [{k: part[k] for k in ("name", "image", "pivot", "amp")
                          if k in part}]}
    path = out / f"{args.name}.json"
    write_json(path, result)
    # the cut's own position on the frame, so a plan can place it without
    # re-deriving offsets from the PNG bounds
    write_json(out / f"{args.name}_meta.json",
               {"offset_px": part.get("offset_px"), "box_px": part.get("box_px"),
                "pivot": part.get("pivot")})
    print(json.dumps({"wrote": str(path), "kind": kind,
                      "part": result["parts"][0]["image"]}, ensure_ascii=False))
    return 0


def cmd_motion(args) -> int:
    from .motion_video import render

    meta = render(args.plan, args.out)
    print(json.dumps(meta, ensure_ascii=False))
    return 0


def cmd_plan(args) -> int:
    """A template plan for one cut part; edit the beats, then render."""
    piece = read_json(args.rig)
    part = piece["parts"][0]
    w = int(args.width)
    h = int(args.height)
    plan = {
        "page": args.page,
        "size": [w, h],
        "bg": args.bg,
        "fps": 20,
        "seconds": 2.4,
        "layers": [
            {"image": args.body, "x": 0, "y": 0},
            {"image": part["image"], "x": int(args.x), "y": int(args.y),
             "pivot": part["pivot"], "beats": [
                 {"t": 0.0, "action": "wave", "amp": part.get("amp", 14.0),
                  "cycles": args.cycles, "dur": 2.4}]},
        ],
    }
    out = ensure_dir(Path(args.out)) / f"{args.page}_plan.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(out)}, ensure_ascii=False))
    return 0


def cmd_leaf(args) -> int:
    """Split a page frame into a flat background plus one layer per figure."""
    from .vision import layers as L

    reader = Path(args.reader)
    boxes = [tuple(float(v) for v in b.split(",")) for b in args.boxes.split(";") if b]
    data = L.build(reader, args.page, args.art, boxes, Path(args.out), use_models=args.models)
    print(json.dumps({k: v for k, v in data.items() if k != "layers"}, ensure_ascii=False))
    return 0


def cmd_help(args) -> int:
    print(__doc__)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("part", help="cut by ink-bounded region (point prompted)")
    g.add_argument("--from", dest="src", required=True)
    g.add_argument("--at", action="append", required=True, help="x,y normalised on the piece")
    g.add_argument("--to", dest="to", required=True)
    g.add_argument("--name", default="part")
    g.add_argument("--tol", type=float, default=26.0)
    g.add_argument("--amp", type=float, default=12.0)
    g.add_argument("--label", default="")
    g.set_defaults(func=cmd_part)

    g = sub.add_parser("probe", help="operator segmentation -> decision table")
    g.add_argument("--from", dest="src", required=True)
    g.add_argument("--point", action="append", help="x,y (normalised) to name a region")
    g.add_argument("--colours", type=int, default=12)
    g.add_argument("--min-area", type=int, default=120)
    g.add_argument("--limit", type=int, default=40)
    g.add_argument("--json")
    g.set_defaults(func=cmd_seg)

    g = sub.add_parser("inspect", help="SAM proposal expressed as region ids")
    g.add_argument("--from", dest="src", required=True)
    g.add_argument("--point", action="append")
    g.add_argument("--box", help="x,y,w,h normalised")
    g.add_argument("--colours", type=int, default=12)
    g.add_argument("--min-area", type=int, default=120)
    g.set_defaults(func=cmd_seg)

    g = sub.add_parser("cut", help="cut by region spec (no rectangle)")
    g.add_argument("--from", dest="src", required=True)
    g.add_argument("--spec", required=True)
    g.add_argument("--to", dest="to", required=True)
    g.add_argument("--name", default="piece")
    g.set_defaults(func=cmd_seg)

    g = sub.add_parser("review", help="machine review of a cut piece")
    g.add_argument("--rig", required=True)
    g.set_defaults(func=cmd_seg)

    g = sub.add_parser("rig-gap", help="cut a round region (missing limb / head)")
    g.add_argument("--from", dest="src", required=True)
    g.add_argument("--to", dest="out", required=True)
    g.add_argument("--cx", type=float, required=True)
    g.add_argument("--cy", type=float, required=True)
    g.add_argument("--r", type=float, required=True)
    g.add_argument("--pivot")
    g.add_argument("--name", default="part")
    g.set_defaults(func=cmd_rig)

    a = sub.add_parser("rig-arm", help="cut a rigid limb by rectangle")
    a.add_argument("--from", dest="src", required=True)
    a.add_argument("--to", dest="out", required=True)
    a.add_argument("--bbox", required=True, help="x,y,w,h normalised")
    a.add_argument("--step", type=float, default=0.0)
    a.add_argument("--name", default="arm_r")
    a.set_defaults(func=cmd_rig)

    m = sub.add_parser("motion", help="render a beats plan to GIF/MP4")
    m.add_argument("--plan", required=True)
    m.add_argument("--out", required=True)
    m.set_defaults(func=cmd_motion)

    p = sub.add_parser("plan", help="write a template plan for a cut part")
    p.add_argument("--rig", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--page", default="frame")
    p.add_argument("--width", type=int, default=360)
    p.add_argument("--height", type=int, default=460)
    p.add_argument("--bg", default="#f6efe2")
    p.add_argument("--x", type=float, default=0)
    p.add_argument("--y", type=float, default=0)
    p.add_argument("--cycles", type=float, default=2.0)
    p.set_defaults(func=cmd_plan)

    l = sub.add_parser("leaf", help="split a frame into background + figure layers")
    l.add_argument("--reader", required=True, help="the reader package directory")
    l.add_argument("--page", required=True)
    l.add_argument("--art", required=True, help="page art, relative to the reader dir")
    l.add_argument("--boxes", required=True, help="'x,y,w,h;x,y,w,h' normalised")
    l.add_argument("--out", required=True)
    l.add_argument("--models", action="store_true")
    l.set_defaults(func=cmd_leaf)

    h = sub.add_parser("help", help="the contract for every verb")
    h.set_defaults(func=cmd_help)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
