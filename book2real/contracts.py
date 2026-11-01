"""The data contract between planning, generation, QC and compositing.

Everything that crosses a module boundary is one of these objects; they
serialise to plain JSON so a run can be audited without reading the code.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"

TRACKS = ("auto", "parallax", "procedural", "i2v", "static")
MOVES = (
    "none", "kenburns", "dolly_in", "dolly_out",
    "pan_lr", "pan_rl", "tilt_up", "tilt_down", "drift",
)


# --------------------------------------------------------------------- shot
@dataclass
class Camera:
    """Virtual camera. `amount` is relative to the frame size (0..1)."""
    move: str = "kenburns"
    amount: float = 0.09
    ease: str = "in_out"
    mode: str = "card"          # card = page card inside a framed backdrop | fill = crop
    parallax: float = 0.45      # how strongly layers separate

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TextCue:
    """One subtitle line, timed relative to its own shot."""
    start: float
    end: float
    text: str


@dataclass
class ShotSpec:
    """One shot of the film — the input of the generation stage."""
    shot_id: str
    page: int
    duration: float = 4.0
    fps: int = 24
    track: str = "auto"
    camera: Camera = field(default_factory=Camera)
    motion: dict = field(default_factory=dict)      # backend-specific parameters
    text_cues: list = field(default_factory=list)
    budget_class: str = "standard"                  # hero | standard | filler
    seed: int = 0
    prompt: str = ""
    # resolved at run time (not part of the authored storyboard)
    page_image: str = ""
    text_mask: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["schema_version"] = SCHEMA_VERSION
        return d

    @staticmethod
    def from_dict(d: dict) -> "ShotSpec":
        cam = d.get("camera") or {}
        if isinstance(cam, str):
            cam = {"move": cam}
        return ShotSpec(
            shot_id=d["shot_id"], page=int(d["page"]),
            duration=float(d.get("duration", 4.0)), fps=int(d.get("fps", 24)),
            track=d.get("track", "auto"),
            camera=Camera(**{k: v for k, v in cam.items() if k in Camera.__annotations__}),
            motion=d.get("motion") or {},
            text_cues=d.get("text_cues") or [],
            budget_class=d.get("budget_class", "standard"),
            seed=int(d.get("seed", 0)),
            prompt=d.get("prompt", ""),
            page_image=d.get("page_image", ""),
            text_mask=d.get("text_mask", ""),
        )

    @property
    def frames(self) -> int:
        return max(1, int(round(self.duration * self.fps)))

    @property
    def hero(self) -> bool:
        return self.budget_class == "hero"


# --------------------------------------------------------------------- clip
@dataclass
class ClipOut:
    """One rendered shot — output of generation, input of QC and compositing."""
    shot_id: str
    page: int
    path: str
    width: int
    height: int
    fps: int
    frames: int
    duration: float
    backend: dict = field(default_factory=dict)      # {id, model, params, seed}
    cost: dict = field(default_factory=dict)         # {wall_s, fps_out}
    qc: dict = field(default_factory=dict)
    status: str = "ok"                               # ok | degraded | placeholder
    fallback_from: list = field(default_factory=list)
    sha256: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ClipOut":
        d = {k: v for k, v in d.items() if k != "schema_version"}
        return ClipOut(**d)


# --------------------------------------------------------------------- qc
@dataclass
class QCVerdict:
    accept: bool
    score: float
    reasons: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------- run
@dataclass
class Manifest:
    book_id: str
    run_id: str
    created_at: str
    config: dict
    shots: list = field(default_factory=list)
    film: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    degradations: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["schema_version"] = SCHEMA_VERSION
        return d


def jsonable(obj: Any) -> Any:
    """Deep conversion for logging / manifest writing."""
    if hasattr(obj, "to_dict"):
        return jsonable(obj.to_dict())
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)
