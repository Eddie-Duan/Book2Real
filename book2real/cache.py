"""Content-addressed store + cache keys.

A shot's cache key is a pure function of everything that could change its
pixels: the page bytes, the shot spec, the backend id and its params, the
film framing, and the model digest. Re-running the same book therefore costs
zero GPU/CPU time — that is where the 15-minute budget comes from.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .util import canonical_json, ensure_dir, sha256_bytes, sha256_file, sha256_json


class CAS:
    def __init__(self, root: str | Path):
        self.root = ensure_dir(root)

    def _dest(self, digest: str, ext: str) -> Path:
        d = self.root / digest[:2]
        ensure_dir(d)
        return d / f"{digest}{ext}"

    def put_bytes(self, data: bytes, ext: str = ".bin") -> str:
        digest = sha256_bytes(data)
        dest = self._dest(digest, ext)
        if not dest.exists():
            dest.write_bytes(data)
        return f"{digest[:2]}/{dest.name}"

    def put_file(self, src: str | Path, ext: str | None = None) -> str:
        src = Path(src)
        digest = sha256_file(src)
        dest = self._dest(digest, ext or src.suffix)
        if not dest.exists():
            shutil.copy2(src, dest)
        return f"{digest[:2]}/{dest.name}"

    def path_of(self, rel: str) -> Path:
        return self.root / rel

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()


def shot_key(
    spec_dict: dict,
    image_digest: str,
    backend_id: str,
    backend_params: dict[str, Any],
    film_dict: dict,
) -> str:
    """Stable 16-hex cache key for one shot."""
    payload = {
        # Bump when the renderer's output changes without the inputs changing.
        "v": "2",
        "spec": spec_dict,
        "img": image_digest,
        "backend": backend_id,
        "params": backend_params,
        "film": film_dict,
    }
    return sha256_json(payload)[:16]
