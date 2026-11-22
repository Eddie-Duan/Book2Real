"""Skill 4 public API.

Backend integration should import from this package, not from a private file:

    from ..skill4 import (
        build_style_card, style_prompt, style_touch_text,
        style_fallback_text, validate_answer,
    )
"""

from .core import (
    build_style_card,
    style_fallback_text,
    style_prompt,
    style_touch_text,
    validate_answer,
)

__all__ = [
    "build_style_card",
    "style_fallback_text",
    "style_prompt",
    "style_touch_text",
    "validate_answer",
]
