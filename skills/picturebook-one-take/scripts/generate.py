# -*- coding: utf-8 -*-
"""Seedance 2.5 flash one-take chain: first frame + ordered extensions + gate/retry."""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from common import (ARK, SEEDANCE_MODEL, ark_get, ark_post, data_url, download,
                    est_video_cny, jread, jwrite, ledger_append, now)
from contract import lint_prompt
from gates import gate_chunk, extract_frames
from prompting import build_prompt


def _asset_path(out: Path, manifest: dict, logical: str) -> Path:
    if logical.startswith("assets/"):
        return out / logical
    entry = manifest.get(f"plate_{logical}") or manifest.get(logical) or {}
    rel = entry.get("plate") or entry.get("head") or ""
    if rel:
        return out / rel
    raise FileNotFoundError(f"asset {logical} not found in {out/'assets/assets.json'}")


def _cast_images(chunk: dict, out: Path, manifest: dict) -> tuple[list[Path], dict[str, int]]:
    paths, mapping = [], {}
    for cid in chunk.get("cast") or []:
        entry = manifest.get(cid) or {}
        rel = entry.get("head")
        if rel and (out / rel).exists():
            mapping[cid] = len(paths) + 1
            paths.append(out / rel)
    return paths, mapping




def _prop_images(chunk: dict, out: Path, bible: dict) -> tuple[list[Path], dict[str, int]]:
    props = {p["id"]: p for p in bible.get("props") or []}
    paths, mapping = [], {}
    pids = list(chunk.get("props") or []) or [p["id"] for p in (bible.get("props") or [])
            if set(chunk.get("pages") or []).intersection(p.get("pages") or [])]
    for pid in pids:
        ref = (props.get(pid) or {}).get("ref")
        if ref and (out / ref).exists() and (out / ref) not in paths:
            mapping[pid] = len(paths) + 1
            paths.append(out / ref)
    return paths, mapping


def _create(payload: dict) -> dict:
    status, data = ark_post("/contents/generations/tasks", payload, timeout=90)
    if status != 200 or not data.get("id"):
        raise RuntimeError(f"Seedance create failed HTTP {status}: {json.dumps(data, ensure_ascii=False)[:800]}")
    return data


def _poll(task_id: str, timeout_s: int = 1800) -> dict:
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        time.sleep(10)
        last = ark_get(f"/contents/generations/tasks/{task_id}")
        status = str(last.get("status") or "")
        print(f"    poll {status} usage={last.get('usage')}", flush=True)
        if status == "succeeded":
            return last
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"Seedance task {task_id} {status}: {json.dumps(last, ensure_ascii=False)[:800]}")
    raise TimeoutError(f"Seedance task {task_id} timeout; last={last.get('status')}")


def generate_chunk(chunk: dict, bible: dict, out: Path, manifest: dict,
                   prev: Path | None, prev_url: str | None, resolution: str,
                   failures: list[dict] | None = None, attempt: int = 1) -> dict:
    cast_imgs, image_map = _cast_images(chunk, out, manifest)
    prop_imgs, prop_map_rel = _prop_images(chunk, out, bible)
    prop_map = {k: len(cast_imgs) + v for k, v in prop_map_rel.items()}
    plate = None
    if chunk.get("first_frame"):
        plate = _asset_path(out, manifest, chunk["first_frame"])
    location_ref = None
    location_img = None
    if chunk.get("location_ref"):
        location_img = _asset_path(out, manifest, chunk["location_ref"])
        location_ref = len(cast_imgs) + len(prop_imgs) + 1
    prompt = build_prompt(chunk, bible, image_map, failures, location_ref, prop_map)
    # write the exact prompt used for audit + lint
    pdir = out / "prompts"; pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{chunk['id']}_attempt{attempt}.txt").write_text(prompt, encoding="utf-8")
    lint = lint_prompt(prompt, chunk, bible)
    jwrite(out / f"{chunk['id']}_attempt{attempt}_lint.json", lint)
    if not lint["passed"]:
        raise RuntimeError(f"prompt lint failed for {chunk['id']}: {lint['failures']}")

    content = [{"type": "text", "text": prompt}]
    if prev is not None:
        if not prev_url:
            raise RuntimeError("extension needs previous video URL; previous result has no URL")
        content.append({"type": "video_url", "video_url": {"url": prev_url}, "role": "reference_video"})
        for p in cast_imgs:
            content.append({"type": "image_url", "image_url": {"url": data_url(p)}, "role": "reference_image"})
        for p in prop_imgs:
            content.append({"type": "image_url", "image_url": {"url": data_url(p)}, "role": "reference_image"})
        if location_img is not None:
            content.append({"type": "image_url", "image_url": {"url": data_url(location_img)}, "role": "reference_image"})
        payload = {"model": SEEDANCE_MODEL, "content": content, "resolution": resolution,
                   "ratio": "adaptive", "duration": int(chunk["duration"]),
                   "generate_audio": True, "omni_reference_task_type": "extend",
                   "watermark": False}
    else:
        if plate is None:
            raise RuntimeError("first chunk needs chunk.first_frame")
        content.append({"type": "image_url", "image_url": {"url": data_url(plate)},
                        "role": "first_frame"})
        payload = {"model": SEEDANCE_MODEL, "content": content, "resolution": resolution,
                   "ratio": "adaptive", "duration": int(chunk["duration"]),
                   "generate_audio": True, "watermark": False}

    task = _create(payload)
    print(f"  created {task['id']}", flush=True)
    info = _poll(task["id"])
    link = (info.get("content") or {}).get("video_url") or ""
    if not link:
        raise RuntimeError(f"task {task['id']} succeeded without video_url")
    dest = out / "chunks" / f"{chunk['id']}.mp4"
    download(link, dest, timeout=900)
    tokens = int((info.get("usage") or {}).get("total_tokens") or 0)
    cny = est_video_cny(tokens, has_video_input=prev is not None)
    ledger_append(out, {"ts": now(), "chunk": chunk["id"], "task_id": task["id"],
                        "model": SEEDANCE_MODEL, "resolution": resolution,
                        "duration_sec": chunk["duration"], "tokens": tokens,
                        "est_cny": cny, "note": prompt[:60]})
    meta = {"chunk": chunk["id"], "task_id": task["id"], "url": link, "tokens": tokens,
            "est_cny": cny, "duration": chunk["duration"], "prompt_file": f"prompts/{chunk['id']}_attempt{attempt}.txt"}
    jwrite(out / f"{chunk['id']}.json", meta)
    return {"path": dest, "url": link, "meta": meta}


def prior_spend(out: Path) -> float:
    """Sum every attempt recorded in cost.csv so --max-cny is a TOTAL budget."""
    f = out / "cost.csv"
    if not f.exists():
        return 0.0
    try:
        rows = list(csv.DictReader(f.open(encoding="utf-8")))
        return round(sum(float(r.get("est_cny") or 0) for r in rows), 3)
    except Exception:  # noqa: BLE001
        return 0.0


def run_chain(bible: dict, script: dict, out: str | Path, resolution: str = "720p",
              max_cny: float = 200.0, start: int = 1, end: int | None = None,
              force: bool = False, max_attempts: int = 1) -> dict:
    out = Path(out)
    manifest = jread(out / "assets" / "assets.json")
    book_pages = sorted((Path("books") / bible["book_id"] / "reader" / "art").glob("p*.jpg"))
    head_refs = {c["id"]: out / c["ref"] for c in bible["characters"] if (out / c["ref"]).exists()}
    chunks = script["chunks"]
    end = end or len(chunks)
    spent = prior_spend(out)
    print(f"  prior spend on this book: CNY {spent:.2f}; budget CNY {max_cny:.2f}", flush=True)
    prev = None
    prev_url = None
    # warm prev from an existing previous chunk
    if start > 1:
        p = out / "chunks" / f"{chunks[start-2]['id']}.mp4"
        if not p.exists():
            raise FileNotFoundError(f"start>{1} needs previous chunk {p}")
        prev = p
        prev_url = (jread(out / f"{chunks[start-2]['id']}.json").get("url") if (out / f"{chunks[start-2]['id']}.json").exists() else None)
        if not prev_url:
            raise RuntimeError("previous chunk JSON has no URL; extension cannot continue")
    reports = []
    for idx in range(start - 1, end):
        chunk = chunks[idx]
        cid = chunk["id"]
        rate = 0.55 if resolution == "480p" else 1.0
        est = float(chunk["duration"]) * rate  # conservative pre-flight estimate
        if spent + est > max_cny:
            raise RuntimeError(f"budget guard: spent {spent:.1f}+{est:.1f} > {max_cny}")
        dest = out / "chunks" / f"{cid}.mp4"
        report_path = out / f"{cid}_gate.json"
        if dest.exists() and report_path.exists() and not force:
            rep = jread(report_path)
            if rep.get("verdict") == "PASS":
                print(f"[{cid}] already PASS, skip", flush=True)
                prev, prev_url = dest, (jread(out / f"{cid}.json").get("url") if (out / f"{cid}.json").exists() else None)
                reports.append(rep)
                continue
        failures = None
        for attempt in range(1, max_attempts + 1):
            print(f"[{cid}] attempt {attempt}/{max_attempts}", flush=True)
            try:
                gen = generate_chunk(chunk, bible, out, manifest, prev, prev_url,
                                     resolution, failures, attempt)
            except Exception as exc:  # noqa: BLE001
                print(f"[{cid}] generation error: {exc}", flush=True)
                failures = [{"code": "generate.error", "message": str(exc),
                             "fix": "上一轮生成失败；保持同样的八块契约，缩短到 25 秒并减少镜头数"}]
                continue
            dest, prev_url_new = gen["path"], gen["url"]
            spent += float(gen["meta"].get("est_cny") or 0)
            plate = None
            if chunk.get("first_frame"):
                try:
                    plate = _asset_path(out, manifest, chunk["first_frame"])
                except Exception:  # noqa: BLE001
                    plate = None
            rep = gate_chunk(chunk, dest, out, prev, book_pages, head_refs, plate,
                             bible.get("language", "zh"), resolution=resolution)
            print(f"[{cid}] gate {rep['verdict']}: {[f['code'] for f in rep['failures']]}", flush=True)
            reports.append(rep)
            if rep["verdict"] == "PASS":
                prev, prev_url = dest, prev_url_new
                break
            failures = rep["failures"]
            if attempt == max_attempts:
                raise RuntimeError(f"{cid} failed gates after {max_attempts} attempts: {failures}")
    return {"reports": reports, "est_cny": round(spent, 3)}