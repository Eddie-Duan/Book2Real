"""Run configuration: film framing, quality thresholds, runtime limits."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class FilmConfig:
    width: int = 1280
    height: int = 720
    fps: int = 24
    card_ratio: float = 0.86          # page card height as a fraction of frame height
    backdrop_blur: float = 0.055      # blur sigma as a fraction of frame width
    backdrop_dim: float = 0.62        # 0..1 brightness of the blurred backdrop
    shadow: float = 0.55
    chapter_fade: float = 0.45        # seconds of fade in/out per shot
    title_seconds: float = 3.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QCConfig:
    min_motion: float = 0.003         # mean |Δ| vs the first frame, 0..1
    max_flicker: float = 0.035
    max_still_ratio: float = 0.75     # fraction of frame pairs in frozen runs
    min_sharpness_ratio: float = 0.55  # laplacian variance on the card vs the page
    min_color_sim: float = 0.65       # histogram intersection with the source page

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuntimeConfig:
    runs_dir: str = "runs"
    books_dir: str = "books"
    cas_dir: str = "cas"
    jobs: int = 2
    budget_s: float = 900.0           # wall-clock budget for one book
    max_retries: int = 1
    allow_external_api: bool = False
    opencv_dir: str = ""              # optional extra sys.path for cv2

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Config:
    film: FilmConfig = field(default_factory=FilmConfig)
    qc: QCConfig = field(default_factory=QCConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    @staticmethod
    def load(path: str | Path | None = None) -> "Config":
        import json

        cfg = Config()
        if path and Path(path).exists():
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if "film" in raw:
                cfg.film = FilmConfig(**{k: v for k, v in raw["film"].items()
                                         if k in FilmConfig.__annotations__})
            if "qc" in raw:
                cfg.qc = QCConfig(**{k: v for k, v in raw["qc"].items()
                                     if k in QCConfig.__annotations__})
            if "runtime" in raw:
                cfg.runtime = RuntimeConfig(**{k: v for k, v in raw["runtime"].items()
                                               if k in RuntimeConfig.__annotations__})
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)
