"""纸间生灵 (repo: Book2Real) — turn picture-book pages into a living book.

cast     : PageScout -> CastClerk -> Rigger -> MotionDirector -> Critic
layers   : depth + SAM outline -> background plate + characters (2.5D parallax)
vision   : optional local models (Depth Anything V2, SD-1.5 inpaint, SAM)
"""
import sys
from pathlib import Path

# Optional, repo-local wheels (pypinyin / jieba / edge-tts) keep the demo offline-installable.
_VENDOR = Path(__file__).resolve().parents[1] / "vendor"
if _VENDOR.exists() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

__version__ = "0.1.0"
SCHEMA_VERSION = "1.0"
