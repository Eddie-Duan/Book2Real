"""Run orchestration: plan -> render (cache, fallback, budget) -> QC -> assemble."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import assemble as am
from .backends import REGISTRY
from .cache import shot_key
from .config import Config
from .contracts import ClipOut, Manifest, ShotSpec, jsonable
from .encode import encode_frames
from .qc import evaluate
from .render import prepare
from .storyboard import plan, timeline
from .util import (EventLog, ensure_dir, human, imread, imwrite, now_iso,
                   run_ffmpeg, sha256_file, timer, write_json)


@dataclass
class ShotContext:
    work: Path
    run_dir: Path
    cache_dir: Path
    deadline: float
    events: EventLog


CHAIN_BY_TRACK = {
    "procedural": ["procedural", "parallax", "static"],
    "parallax": ["parallax", "static"],
    "pregen": ["pregen", "parallax", "static"],
    "static": ["static"],
    "i2v": ["pregen", "parallax", "static"],
}


class Runner:
    def __init__(self, cfg: Config, out_root: str | Path = "runs", *,
                 jobs: int = 2, b_track: bool = True, force: bool = False,
                 verbose: bool = True):
        self.cfg = cfg
        self.out_root = Path(out_root).resolve()
        self.jobs = max(1, jobs)
        self.b_track = b_track
        self.force = force
        self.verbose = verbose
        self.layers = True

    # ------------------------------------------------------------------ api
    def run(self, book_dir: str | Path) -> tuple[Manifest, Path]:
        t0 = time.perf_counter()
        book_dir = Path(book_dir).resolve()
        meta, shots = plan(book_dir, fps=self.cfg.film.fps, b_track=self.b_track)
        # Printed pages with text are kept on one rigid plate by default for
        # real books: decomposing them makes glyphs ghost (see docs/HANDOFF.md).
        self.layers = bool(meta.get("layers", True))

        run_id = time.strftime("%Y%m%d-%H%M%S")
        base = ensure_dir(self.out_root / meta["book_id"])
        run_dir = ensure_dir(base / run_id)
        cache_dir = ensure_dir(base / "_cache")
        ev = EventLog(run_dir / "events.jsonl")
        deadline = time.time() + float(self.cfg.runtime.budget_s)
        ev.emit("run.start", book=meta["book_id"], run_id=run_id, shots=len(shots),
                b_track=self.b_track, layers=self.layers,
                budget_s=self.cfg.runtime.budget_s)
        self._say(f"[b2r] {meta['book_id']} · {len(shots)} shots · run {run_id}")

        ctx = ShotContext(work=run_dir / "work", run_dir=run_dir,
                          cache_dir=cache_dir, deadline=deadline, events=ev)
        ensure_dir(ctx.work)

        clips: dict[str, ClipOut] = {}
        timings: dict[str, float] = {}
        with ThreadPoolExecutor(max_workers=self.jobs) as ex:
            futs = {ex.submit(self._one, s, ctx): s for s in shots}
            for fut in as_completed(futs):
                spec = futs[fut]
                clip = fut.result()
                clips[spec.shot_id] = clip
                timings[spec.shot_id] = clip.cost.get("wall_s", 0.0)
                self._say(f"  {spec.shot_id} p{spec.page:02d} {clip.backend.get('id'):<10} "
                          f"{clip.duration:4.1f}s qc={clip.qc.get('score', 0):.2f} "
                          f"{clip.status:<9} {human(clip.cost.get('wall_s', 0))}")

        ordered = [clips[s.shot_id] for s in shots if s.shot_id in clips]
        film = self._assemble(meta, shots, ordered, ctx)

        wall = round(time.perf_counter() - t0, 2)
        stats = {
            "shots": len(ordered),
            "pages": len({s.page for s in shots}),
            "coverage": round(len({s.page for s in shots}) / max(1, len(meta["pages"])), 4),
            "cache_hits": sum(1 for c in ordered if c.backend.get("cached")),
            "fallbacks": sum(1 for c in ordered if c.fallback_from),
            "avg_qc": round(float(np.mean([c.qc.get("score", 0) for c in ordered])) if ordered else 0, 3),
            "wall_s": wall,
            "film_seconds": film.get("duration", 0),
            "track_mix": {b: sum(1 for c in ordered if c.backend.get("id") == b)
                          for b in {c.backend.get("id") for c in ordered}},
        }
        manifest = Manifest(
            book_id=meta["book_id"], run_id=run_id, created_at=now_iso(),
            config=self.cfg.to_dict(),
            shots=[jsonable(c) for c in ordered],
            film=film,
            timings=timings,
            degradations=[{"shot": c.shot_id, "from": c.fallback_from}
                          for c in ordered if c.fallback_from],
            stats=stats,
        )
        write_json(run_dir / "manifest.json", manifest.to_dict())
        write_json(run_dir / "timeline.json", film.get("chapters", []))
        ev.emit("run.done", wall_s=wall, stats=stats)
        self._say(f"[b2r] done in {human(wall)} · film "
                  f"{film.get('duration', 0):.1f}s · avg qc {stats['avg_qc']:.2f}")
        return manifest, run_dir

    # --------------------------------------------------------------- shot
    def _one(self, spec: ShotSpec, ctx: ShotContext) -> ClipOut:
        img = imread(spec.page_image)
        page_digest = sha256_file(spec.page_image)
        fill = spec.camera.mode == "fill"
        assets = prepare(img, self.cfg.film, layers=self.layers, fill=fill)
        if fill:
            card_box = None
        else:
            W, H = self.cfg.film.width, self.cfg.film.height
            card_box = ((W - assets.card_w) / 2.0 / W,
                        (H - assets.card_h) / 2.0 / H,
                        assets.card_w / W, assets.card_h / H)

        params = {"film": self.cfg.film.to_dict()}
        key = shot_key(spec.to_dict(), page_digest, spec.track, params,
                       self.cfg.film.to_dict())
        cdir = ensure_dir(ctx.cache_dir / key)
        meta_p = cdir / "clip.json"
        if not self.force and meta_p.exists():
            try:
                c = ClipOut.from_dict(jsonable(_read_json(meta_p)))
                if (self.out_root / c.path).exists():
                    c.backend = {**c.backend, "cached": True}
                    c.cost = {**c.cost, "wall_s": 0.0}
                    return c
            except Exception:
                pass

        chain = CHAIN_BY_TRACK.get(spec.track, CHAIN_BY_TRACK["parallax"])
        if time.time() > ctx.deadline:
            chain = ["static"]
            ctx.events.emit("budget.exceeded", shot=spec.shot_id)

        tried: list[str] = []
        last_err = ""
        for bid in chain:
            backend = REGISTRY[bid]
            ok, why = backend.available()
            if not ok:
                last_err = why
                continue
            tried.append(bid)
            try:
                with timer() as tb:
                    res = backend.render(spec, assets, img, self.cfg.film,
                                         src_dir=Path(spec.page_image).parent.parent)
                    clip_p = cdir / "clip.mp4"
                    if res.video_path is not None:
                        self._normalize(Path(res.video_path), clip_p, spec)
                    else:
                        encode_frames(res.frames, clip_p, spec.fps,
                                      (self.cfg.film.width, self.cfg.film.height))
                verdict = evaluate(clip_p, spec, img, self.cfg.qc,
                                   card_box=card_box, fill=fill)
                if not verdict.accept and len(tried) == 1 and bid in ("procedural", "parallax"):
                    # one cheap retry with a gentler move before degrading the shot
                    spec2 = ShotSpec.from_dict(spec.to_dict())
                    spec2.camera.amount *= 0.7
                    retry_p = cdir / "clip_retry.mp4"
                    with timer() as tb2:
                        res = backend.render(spec2, assets, img, self.cfg.film,
                                             src_dir=Path(spec.page_image).parent.parent)
                        if res.video_path is not None:
                            self._normalize(Path(res.video_path), retry_p, spec)
                        else:
                            encode_frames(res.frames, retry_p, spec.fps,
                                          (self.cfg.film.width, self.cfg.film.height))
                    v2 = evaluate(retry_p, spec2, img, self.cfg.qc,
                                  card_box=card_box, fill=fill)
                    ctx.events.emit("qc.retry", shot=spec.shot_id, backend=bid,
                                    before=verdict.score, after=v2.score)
                    if v2.score >= verdict.score:
                        retry_p.replace(clip_p)
                        verdict, spec = v2, spec2
                        tb["wall_s"] = round(tb["wall_s"] + tb2["wall_s"], 3)
                    retry_p.unlink(missing_ok=True)

                rel = clip_p.relative_to(self.out_root).as_posix()
                clip = ClipOut(
                    shot_id=spec.shot_id, page=spec.page, path=rel,
                    width=self.cfg.film.width, height=self.cfg.film.height,
                    fps=spec.fps, frames=spec.frames, duration=round(spec.frames / spec.fps, 3),
                    backend={"id": bid, "label": backend.caps.label,
                             "cost": backend.caps.cost, "params": spec.camera.to_dict(),
                             "seed": spec.seed, "info": res.info},
                    cost={"wall_s": tb["wall_s"]},
                    qc=verdict.to_dict(),
                    status="ok" if verdict.accept else "degraded",
                    fallback_from=[] if tried[0] == bid else [tried[0]],
                    sha256=sha256_file(clip_p),
                )
                write_json(meta_p, clip.to_dict())
                ctx.events.emit("shot.done", shot=spec.shot_id, backend=bid,
                                qc=verdict.score, accept=verdict.accept, wall_s=tb["wall_s"])
                return clip
            except Exception as exc:                      # noqa: BLE001 - isolation is the point
                last_err = f"{type(exc).__name__}: {exc}"
                ctx.events.emit("shot.fail", shot=spec.shot_id, backend=bid, error=last_err)
                self._say(f"  ! {spec.shot_id} {bid}: {last_err}")

        raise RuntimeError(f"all backends failed for {spec.shot_id}: {last_err}")

    def _normalize(self, src: Path, dst: Path, spec: ShotSpec) -> None:
        """Bring an external clip onto the film grid (size, fps, duration)."""
        run_ffmpeg([
            "-i", str(src),
            "-t", f"{spec.duration:.3f}",
            "-vf", f"scale={self.cfg.film.width}:{self.cfg.film.height}:"
                   f"force_original_aspect_ratio=decrease,"
                   f"pad={self.cfg.film.width}:{self.cfg.film.height}:(ow-iw)/2:(oh-ih)/2:black,"
                   f"fps={spec.fps},setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an", str(dst),
        ])

    # ----------------------------------------------------------- assemble
    def _assemble(self, meta: dict, shots: list[ShotSpec], clips: list[ClipOut],
                  ctx: ShotContext) -> dict:
        work = ctx.work
        ensure_dir(work)
        parts: list[Path] = []
        title_seconds = 0.0

        if self.cfg.film.title_seconds > 0:
            bg = Path(shots[0].page_image) if shots else None
            tc = am.make_title_clip(meta.get("title", meta["book_id"]),
                                    meta.get("subtitle", "Book2Real · 纸书成片"),
                                    bg, work / "title.mp4", self.cfg.film,
                                    self.cfg.film.title_seconds)
            parts.append(tc)
            title_seconds = self.cfg.film.title_seconds

        cues: list[tuple] = []
        t = title_seconds
        for s, c in zip(shots, clips):
            parts.append(self.out_root / c.path)
            for cue in s.text_cues:
                cues.append((t + float(cue.get("start", 0.2)),
                             t + float(cue.get("end", c.duration - 0.25)),
                             cue.get("text", "")))
            t += c.duration

        end = am.make_end_clip("本片由 AI 辅助生成 · Book2Real", work / "end.mp4",
                               self.cfg.film, 2.0)
        parts.append(end)

        raw = am.concat(parts, work / "film_raw.mp4", work)
        ass = am.write_ass(cues, work / "subs.ass", self.cfg.film) if cues else None
        out = ctx.run_dir / "film.mp4"
        am.finish(raw, out, self.cfg.film, ass=ass, audio=None)

        chapters = timeline(shots, {c.shot_id: c for c in clips},
                            title_seconds=title_seconds)
        total = t + 2.0
        thumbs = self._thumbs(shots, ctx.run_dir)

        return {"path": out.relative_to(self.out_root).as_posix(),
                "duration": round(total, 2), "width": self.cfg.film.width,
                "height": self.cfg.film.height, "fps": self.cfg.film.fps,
                "title_seconds": title_seconds, "chapters": chapters,
                "thumbs": thumbs, "bytes": out.stat().st_size}

    def _thumbs(self, shots: list[ShotSpec], run_dir: Path) -> list[str]:
        from . import imgops as I

        out = []
        d = ensure_dir(run_dir / "thumbs")
        for s in shots:
            p = d / f"p{s.page:02d}.jpg"
            if not p.exists():
                img = imread(s.page_image)
                h, w = img.shape[:2]
                k = 420.0 / max(h, w)
                imwrite(p, I.resize(img, int(w * k), int(h * k)))
            out.append(p.relative_to(self.out_root).as_posix())
        return out

    # ------------------------------------------------------------- helpers
    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)


def _read_json(p: Path) -> dict:
    import json

    return json.loads(Path(p).read_text(encoding="utf-8"))
