"""Book2Real — turn static picture-book pages into a playable story film.

Track A  parallax     : layered 2.5D camera move from a single page image (CPU, deterministic)
Track B  micro-motion : procedural light/particle/breathe effects; local I2V adapter is pluggable
"""
import sys
from pathlib import Path

# Optional, repo-local wheels (pypinyin / jieba / edge-tts) keep the demo offline-installable.
_VENDOR = Path(__file__).resolve().parents[1] / "vendor"
if _VENDOR.exists() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

__version__ = "0.1.0"
SCHEMA_VERSION = "1.0"
