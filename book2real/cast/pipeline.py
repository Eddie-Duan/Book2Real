"""Run the whole cast chain for one book.

    PageScout -> CastClerk -> Rigger -> MotionDirector -> Critic

Artifacts land in the reader package so the web stage can use them directly:

    reader/cast/entities.json      what PageScout saw, per page
    reader/cast/rig/<cid>.json     parts + pivots + confidence
    reader/cast/critic.json        per-target verdicts
    reader/cast/report.json        timings, counts, degradations
    reader/characters/*            portraits and puppet parts
    reader/characters/characters.json   the card the reader actually loads
    reader/motion/<page>.json      one beat sheet per screen
"""
from __future__ import annotations

import time
from pathlib import Path

from ..util import ensure_dir, now_iso, read_json, write_json
from . import castclerk, critic, motion, pagescout, rigger
from .contracts import Rig


def _prune(char_dir: Path, manifest_path: Path, kept: set[str],
           attempted: set[str]) -> int:
    """Delete cast-generated files that this run no longer references.

    Only names that a previous run recorded, or that this run just wrote, are
    eligible — hand-made files in the same folder are never touched.
    """
    manifest_path = Path(manifest_path)
    old_files: list[str] = []
    if manifest_path.exists():
        previous = read_json(manifest_path)
        if not isinstance(previous, dict) or not isinstance(previous.get("files"), list):
            raise ValueError(f"invalid generated-file manifest: {manifest_path}")
        old_files = previous["files"]
        if any(not isinstance(n, str) or Path(n).name != n or n in {".", ".."}
               or "/" in n or "\\" in n for n in old_files):
            raise ValueError(f"unsafe filename in generated-file manifest: {manifest_path}")

    removed = 0
    for name in (set(old_files) | set(attempted)) - set(kept):
        f = Path(char_dir) / name
        if f.is_file():
            f.unlink()
            removed += 1
    write_json(manifest_path, {"files": sorted(kept)})
    return removed


def run(book_dir: str | Path, *, force: bool = False, max_rigs: int = 3,
        on_progress=None) -> dict:
    t0 = time.perf_counter()
    book_dir = Path(book_dir).resolve()
    reader = book_dir / "reader"
    if not (reader / "book.json").exists():
        raise FileNotFoundError(
            f"{reader / 'book.json'} missing — run `python -m book2real analyze "
            f"--book {book_dir}` first")
    book = read_json(reader / "book.json")
    cur_path = book_dir / "curation.json"
    curation = read_json(cur_path) if cur_path.exists() else {}
    cast_dir = ensure_dir(reader / "cast")
    char_dir = ensure_dir(reader / "characters")
    motion_dir = ensure_dir(reader / "motion")

    def step(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    # ------------------------------------------------------------- PageScout
    step("PageScout: 找页面里的人物")
    scan = pagescout.scan(book, book_dir, curation)
    write_json(cast_dir / "entities.json", scan)

    # ------------------------------------------------------------- CastClerk
    step("CastClerk: 合并同一角色")
    clerk = castclerk.build(scan, book, curation, book_dir, char_dir)
    cards = clerk["characters"]
    write_json(cast_dir / "clerk.json", clerk)
    attempted = set(clerk.get("generated_files", []))

    def remember_rig_files(rig: Rig) -> None:
        for name in [rig.art, *(part.image for part in rig.parts)]:
            if name.startswith("characters/"):
                attempted.add(Path(name).name)

    # ---------------------------------------------------------------- Rigger
    step("Rigger: 切件 + 关节")
    assignments = clerk.get("assignments", {})
    page_art = {p["id"]: p.get("art_page") or p.get("art", "") for p in book["pages"]}
    # the one thing a person may add: "this cut is character X" (see README)
    claims: dict[str, str] = {}
    for ch in curation.get("characters", []):
        cid = ch.get("id")
        want = ch.get("rig_from") or ch.get("rig_page") or ""
        if cid and want:
            claims[cid] = str(want)

    def claimed_box(cid: str) -> dict | None:
        want = claims.get(cid)
        if not want:
            return None
        page = next((p for p in book["pages"] if p["id"] == want or want in p["id"]), None)
        if not page:
            return None
        ents = (scan["pages"].get(page["id"]) or {}).get("entities", [])
        if not ents:
            return None
        return {"page": page["id"], "box": max(ents, key=lambda e: e["area"])["box"],
                "source": "claimed"}

    def candidates_for(cid: str) -> list[dict]:
        """Pages where PageScout itself named this character, biggest first."""
        out: list[dict] = []
        claim = claimed_box(cid)
        if claim:
            out.append(claim)
        ranked = sorted(assignments.get(cid, []),
                        key=lambda a: -(a["area"] * a["confidence"]))
        for a in ranked:
            if all(a["page"] != o["page"] for o in out):
                out.append({"page": a["page"], "box": a["box"], "source": "detected"})
        return out[:3]

    rank = {"paper_puppet": 3, "one_arm": 2, "head_nod": 1, "portrait": 0}
    rigs: dict[str, Rig] = {}
    rig_pages: dict[str, str] = {}
    verdicts = []
    targets = [c["id"] for c in cards if candidates_for(c["id"])][:max(1, max_rigs)]
    # a claimed character is always rigged, even past the budget
    for cid in claims:
        if cid not in targets and any(c["id"] == cid for c in cards):
            targets.insert(0, cid)
    for cid in targets:
        cands = candidates_for(cid)
        if not cands:
            continue
        best: tuple[float, Rig, object, dict] | None = None
        for cand in cands:
            rig = rigger.build(reader, char_dir, cid, page_art.get(cand["page"], ""),
                               cand["box"])
            remember_rig_files(rig)
            card = next(c for c in cards if c["id"] == cid)
            verdict = critic.judge_character(reader, card, book, rig=rig)
            score = rank.get(rig.kind, 0) + rig.confidence * 0.1
            if verdict.accept and rig.kind != "portrait":
                best = (score, rig, verdict, cand)
                break
            if best is None or score > best[0]:
                best = (score, rig, verdict, cand)
        assert best is not None
        score, rig, verdict, cand = best
        if not verdict.accept and rig.kind != "portrait":
            verdict.reasons.append(f"downgraded {rig.kind} -> portrait")
            rig = Rig(kind="portrait", confidence=rig.confidence, art=rig.art,
                      art_w=rig.art_w, art_h=rig.art_h, key=rig.key,
                      notes=rig.notes + ["critic rejected"])
        rigs[cid] = rig
        rig_pages[cid] = cand["page"]
        card = next(c for c in cards if c["id"] == cid)
        card["rig"] = rig.to_dict()
        verdicts.append(verdict.to_dict())
        write_json(cast_dir / "rig" / f"{cid}.json", rig.to_dict())
        preview = rigger.preview(reader, char_dir, rig, cid)
        if preview:
            card["preview"] = preview
            attempted.add(Path(preview).name)

    # --------------------------------------------------------- MotionDirector
    step("MotionDirector: 写动作 beat")
    plans = motion.plan_book(book, cards, rigs, rig_pages)
    for pid, plan in plans.items():
        write_json(motion_dir / f"{pid}.json", plan)

    # ------------------------------------------------- anonymous rig candidates
    # Identity is not the rigger's job, but cutting is: every unnamed figure big
    # enough to animate gets a rig so a human (or a model) only has to say "this
    # one is Lola" — see curation.json `rig_from`.
    step("Rigger: 无名角色的候选切件")
    anon_dir = ensure_dir(cast_dir / "candidates")
    anon: list[dict] = []
    seen_pages: set[str] = set()
    for cand in clerk.get("candidates", []):
        if len(anon) >= 3 or cand["page"] in seen_pages:
            continue
        seen_pages.add(cand["page"])
        cid = f"anon_{cand['page']}".replace("-", "_")
        rig = rigger.build(reader, char_dir, cid, page_art.get(cand["page"], ""), cand["box"])
        remember_rig_files(rig)
        verdict = critic.judge_character(reader, {"id": cid, "name": "", "role": "",
                                                  "desc": ""}, book, rig=rig)
        if not verdict.accept or not rig.parts:
            continue
        preview = rigger.preview(reader, char_dir, rig, cid)
        if preview:
            attempted.add(Path(preview).name)
        payload = {"id": cid, "page": cand["page"], "box": cand["box"],
                   "area": cand["area"], "rig": rig.to_dict(), "preview": preview,
                   "verdict": verdict.to_dict()}
        write_json(anon_dir / f"{cid}.json", payload)
        anon.append(payload)

    # ---------------------------------------------------------------- Critic
    step("Critic: 验收")
    md_verdicts = [c.to_dict() for c in critic.judge_motion(plans)]
    coverage, missing = critic.text_coverage(book, cards)

    # ------------------------------------------------------------- artifacts
    artifacts = {
        "version": 2,
        "generator": "cast",
        "built_at": now_iso(),
        "characters": cards,
        "portraits": clerk["portraits"],
        "candidates": anon,
        "motion_index": {pid: f"motion/{pid}.json" for pid in plans},
    }
    write_json(char_dir / "characters.json", artifacts)
    kept = set()
    for rec in [*artifacts["characters"], *artifacts["candidates"]]:
        rig = rec.get("rig") or {}
        for name in (rec.get("portrait"), rec.get("preview"), rig.get("art"),
                     *(p.get("image") for p in rig.get("parts", []))):
            if isinstance(name, str) and name.startswith("characters/"):
                kept.add(Path(name).name)
    pruned = _prune(char_dir, cast_dir / "managed_character_files.json", kept, attempted)

    accepted = sum(1 for v in verdicts if v["accept"])
    report = {
        "book": book.get("book_id", book_dir.name),
        "built_at": now_iso(),
        "seconds": round(time.perf_counter() - t0, 2),
        "stages": {
            "PageScout": {"pages": len(scan["pages"]),
                          "degraded": len(scan["degraded_pages"]),
                          "entities": sum(len(p["entities"]) for p in scan["pages"].values())},
            "CastClerk": clerk["stats"],
            "Rigger": {cid: {"kind": rigs[cid].kind, "parts": len(rigs[cid].parts),
                             "confidence": rigs[cid].confidence} for cid in rigs},
            "RiggerCandidates": {"accepted": len(anon),
                                 "considered": min(3, len(clerk.get("candidates", [])))},
            "MotionDirector": {"plans": len(plans),
                               "beats": sum(len(p["beats"]) for p in plans.values())},
            "Critic": {"characters_accepted": accepted, "characters_judged": len(verdicts),
                       "text_coverage": round(coverage, 3), "missing": missing},
        },
        "pruned_files": pruned,
        "verdicts": {"characters": verdicts, "motion": md_verdicts},
    }
    write_json(cast_dir / "critic.json", report["verdicts"])
    write_json(cast_dir / "report.json", report)
    step("完成")
    return report
