"""Download / report the optional model weights for the 8 GB demo.

    python -m book2real models            # report what is cached / installed
    python -m book2real models --fetch    # download the demo tier

Demo tier (fits an 8 GB laptop GPU):

    depth    Depth Anything V2 small          95 MB    ~0.3 s / page
    inpaint  SD-1.5 inpainting               4.0 GB    ~1.5 s / page @512
    sam      MobileSAM / SAM ViT-B (optional) 40 MB / 1.1 GB   SAM outline upgrade

Nothing here is required: every caller falls back to a deterministic path when a
model is missing, and `book2real doctor` reports what is available.
"""
from __future__ import annotations

import glob
import importlib
import os
import time
from pathlib import Path

MODELS = [
    # key, repo, weight file inside the snapshot, description
    ("depth", "depth-anything/Depth-Anything-V2-Small-hf", "model.safetensors",
     "Depth Anything V2 small: 2.5D layer amounts"),
    ("inpaint", "stable-diffusion-v1-5/stable-diffusion-inpainting",
     "unet/diffusion_pytorch_model.safetensors",
     "SD-1.5 inpainting: repair a cut edge / erase a limb"),
    ("sam", "facebook/sam-vit-base", "model.safetensors",
     "SAM ViT-B: turn a figure box into a real outline (optional)"),
]


def _snapshot(repo: str) -> Path | None:
    hub = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    base = Path(hub) / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots"
    snaps = sorted((Path(p) for p in glob.glob(str(base / "*"))),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return snaps[0] if snaps else None


def _weights(repo: str, rel: str, min_mb: float = 50.0) -> str:
    """A weight file counts only if it is actually there at full size."""
    snap = _snapshot(repo)
    if snap is None:
        return ""
    p = snap / rel
    if p.exists() and p.stat().st_size >= min_mb * 2**20:
        return str(p)
    # some repos ship .bin instead of .safetensors
    alt = p.with_suffix(".bin")
    if alt.exists() and alt.stat().st_size >= min_mb * 2**20:
        return str(alt)
    return ""


def status() -> list[dict]:
    out = []
    for key, repo, rel, desc in MODELS:
        mod = {"depth": "book2real.vision.depth", "inpaint": "book2real.vision.inpaint",
               "sam": "book2real.vision.sam"}[key]
        try:
            m = importlib.import_module(mod)
            deps = bool(getattr(m, "available", lambda: True)())
        except Exception:
            deps = False
        path = _weights(repo, rel)
        out.append({"key": key, "repo": repo, "desc": desc, "cache": path,
                    "deps": deps, "installed": bool(path) and deps})
    return out


def fetch(keys: list[str] | None = None) -> int:
    from huggingface_hub import snapshot_download

    for key, repo, rel, _desc in MODELS:
        if keys and key not in keys:
            continue
        if _weights(repo, rel):
            print(f"[ok] {key:<8} cached  {repo}")
            continue
        t0 = time.time()
        print(f"[..] {key:<8} fetching {repo}  ({rel})")
        try:
            snapshot_download(repo, allow_patterns=["*.json", "*.txt", rel,
                                                    rel.replace(".safetensors", ".bin")])
            ok = bool(_weights(repo, rel))
            print(f"[{'ok' if ok else 'FAIL'}] {key:<8} {round(time.time() - t0, 1)}s  {repo}")
            if not ok:
                return 1
        except Exception as exc:                      # noqa: BLE001
            print(f"[FAIL] {key}: {exc.__class__.__name__}: {exc}")
            return 1
    return 0
