"""Contracts shared by the cast agents.

PageScout -> Entity, CastClerk -> CharacterCard, Rigger -> Rig,
MotionDirector -> MotionPlan, Critic -> Critique. Every agent takes plain
JSON-able data in and out so each stage can be rerun, cached or replaced.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

Box = tuple[float, float, float, float]                   # x, y, w, h (normalised)


def _box(v: Any) -> Box:
    if v is None:
        return (0.0, 0.0, 0.0, 0.0)
    return (float(v[0]), float(v[1]), float(v[2]), float(v[3]))


@dataclass
class Entity:
    """One figure-ish blob found on one page."""

    id: str
    page: str
    box: Box = (0.0, 0.0, 0.0, 0.0)
    kind: str = "unknown"                                 # character | animal | object
    name: str = ""
    label: str = ""
    desc: str = ""
    area: float = 0.0                                     # fraction of the page
    aspect: float = 0.0                                   # width / height in pixels
    fill: float = 0.0                                     # blob area / box area
    colors: list[list[int]] = field(default_factory=list)  # 3 dominant RGB values
    signature: list[float] = field(default_factory=list)
    importance: float = 0.0
    confidence: float = 0.0
    name_source: str = "none"                             # curation | label | text | none

    def to_dict(self) -> dict:
        d = asdict(self)
        d["box"] = list(self.box)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Entity":
        e = cls(id=d["id"], page=d["page"])
        for k, v in d.items():
            if k in ("id", "page"):
                continue
            setattr(e, k, _box(v) if k == "box" else v)
        return e


@dataclass
class RigPart:
    name: str                                             # body | arm_l | arm_r | head
    image: str                                            # path relative to reader/
    pivot: tuple[float, float] = (0.5, 0.5)
    amp: float = 0.0                                      # safe rotation, degrees

    def to_dict(self) -> dict:
        return {"name": self.name, "image": self.image,
                "pivot": [round(self.pivot[0], 4), round(self.pivot[1], 4)],
                "amp": self.amp}


@dataclass
class Rig:
    kind: str = "none"                                    # paper_puppet | head_nod | portrait | none
    confidence: float = 0.0
    art: str = ""                                         # path relative to reader/
    art_w: int = 0
    art_h: int = 0
    key: tuple[int, int, int] = (250, 247, 240)           # page background behind the cut
    parts: list[RigPart] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def part(self, name: str) -> RigPart | None:
        return next((p for p in self.parts if p.name == name), None)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "confidence": round(self.confidence, 3),
                "art": self.art, "art_w": self.art_w, "art_h": self.art_h,
                "key": list(self.key),
                "parts": [p.to_dict() for p in self.parts], "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict) -> "Rig":
        r = cls(kind=d.get("kind", "none"), confidence=float(d.get("confidence", 0.0)),
                art=d.get("art", ""), art_w=int(d.get("art_w", 0)),
                art_h=int(d.get("art_h", 0)), notes=list(d.get("notes", [])))
        if d.get("key"):
            r.key = tuple(int(v) for v in d["key"])
        for p in d.get("parts", []):
            r.parts.append(RigPart(p["name"], p["image"],
                                   (float(p["pivot"][0]), float(p["pivot"][1])),
                                   float(p.get("amp", 0.0))))
        return r


@dataclass
class CharacterCard:
    id: str
    name: str
    role: str = ""
    desc: str = ""
    pages: list[str] = field(default_factory=list)
    portrait: str = ""
    confidence: float = 0.0
    evidence: str = ""
    aliases: list[str] = field(default_factory=list)
    rig: Rig = field(default_factory=Rig)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rig"] = self.rig.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CharacterCard":
        c = cls(id=d["id"], name=d.get("name", d["id"]), role=d.get("role", ""),
                desc=d.get("desc", ""), pages=list(d.get("pages", [])),
                portrait=d.get("portrait", ""), confidence=float(d.get("confidence", 0.0)),
                evidence=d.get("evidence", ""), aliases=list(d.get("aliases", [])))
        if d.get("rig"):
            c.rig = Rig.from_dict(d["rig"])
        return c


@dataclass
class Beat:
    t: float
    action: str                                           # idle | wave | point | nod | shrug | bounce | speak
    dur: float = 1.2
    amp: float = 0.0
    cycles: float = 1.0
    side: str = ""                                        # left | right
    text: str = ""
    loop: bool = False

    def to_dict(self) -> dict:
        d = {"t": round(self.t, 2), "action": self.action, "dur": round(self.dur, 2)}
        if self.amp:
            d["amp"] = round(self.amp, 1)
        if self.cycles and self.action in ("wave", "bounce", "shrug", "nod"):
            d["cycles"] = round(self.cycles, 2)
        if self.side:
            d["side"] = self.side
        if self.text:
            d["text"] = self.text
        if self.loop:
            d["loop"] = True
        return d


@dataclass
class MotionPlan:
    page: str
    character: str
    duration: float = 3.0
    emotion: str = "neutral"
    beats: list[Beat] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"page": self.page, "character": self.character,
                "duration": round(self.duration, 2), "emotion": self.emotion,
                "beats": [b.to_dict() for b in self.beats]}

    @classmethod
    def from_dict(cls, d: dict) -> "MotionPlan":
        p = cls(page=d["page"], character=d.get("character", ""),
                duration=float(d.get("duration", 3.0)), emotion=d.get("emotion", "neutral"))
        for b in d.get("beats", []):
            p.beats.append(Beat(t=float(b["t"]), action=b["action"],
                                dur=float(b.get("dur", 1.2)), amp=float(b.get("amp", 0.0)),
                                cycles=float(b.get("cycles", 1.0)), side=b.get("side", ""),
                                text=b.get("text", ""), loop=bool(b.get("loop", False))))
        return p


@dataclass
class Critique:
    target: str                                           # character id or page id
    accept: bool
    scores: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"target": self.target, "accept": self.accept,
                "scores": {k: round(v, 3) for k, v in self.scores.items()},
                "reasons": self.reasons}
