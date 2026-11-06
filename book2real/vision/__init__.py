"""Optional model backends for the 8 GB demo machine.

The cast chain is pure geometry + rules and always works offline. This package
adds the three models the L1/L2 plan needs, each behind a lazy loader so a
missing model only degrades that one step instead of breaking the chain:

    depth   Depth Anything V2 (small)  -> a parallax map for 2.5D layers
    inpaint Stable Diffusion 1.5       -> erase a limb / repair a cut edge
    faces   tiny face cascade          -> keep whole heads inside portraits

Every loader returns ``None`` (and the caller falls back to the deterministic
path) when torch or the weights are missing. Nothing here downloads at import
time: `python -m book2real models` is the one command that fetches weights.
"""
from __future__ import annotations

import os

ROOT_WEIGHTS = "models"


def weights_dir() -> str:
    """Where downloaded weights live (override with BOOK2REAL_WEIGHTS)."""
    env = os.environ.get("BOOK2REAL_WEIGHTS")
    if env:
        return env
    from pathlib import Path

    return str(Path(__file__).resolve().parents[1] / ROOT_WEIGHTS)


def cuda_free_gb() -> float:
    try:
        import torch

        if not torch.cuda.is_available():
            return 0.0
        free, _total = torch.cuda.mem_get_info()
        return free / 2**30
    except Exception:
        return 0.0


_last_error: str = ""


def last_error() -> str:
    return _last_error


def _fail(msg: str) -> None:
    global _last_error
    _last_error = msg
