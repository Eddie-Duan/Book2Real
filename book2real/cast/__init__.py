"""Cast agents for the interactive reader.

PageScout -> CastClerk -> Rigger -> MotionDirector -> Critic, wired together by
`pipeline.run`. See docs/AGENTS.md for the contracts and the degradation rules.
"""
from __future__ import annotations

from .castclerk import build as build_cast
from .contracts import Beat, CharacterCard, Critique, Entity, MotionPlan, Rig, RigPart
from .critic import judge_character, judge_motion, text_coverage
from .motion import plan as plan_motion
from .pagescout import scan as scan_pages
from .pipeline import run
from .rigger import build as build_rig

__all__ = [
    "Beat", "CharacterCard", "Critique", "Entity", "MotionPlan", "Rig", "RigPart",
    "build_cast", "build_rig", "judge_character", "judge_motion", "plan_motion",
    "run", "scan_pages", "text_coverage",
]
