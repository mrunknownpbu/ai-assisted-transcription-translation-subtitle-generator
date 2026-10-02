"""Central, environment-configurable constraints for final subtitle cues.

These values deliberately describe rendering, not ASR.  The transcript keeps
the decoder's word timing intact; source and target segmentation consume the
same constraints when they decide how to display it.
"""

from __future__ import annotations

import os


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# The existing seven-second maximum remains the compatibility default.  It is
# now one shared value rather than independent source/target/QC constants.
MIN_CUE_DURATION = _positive_float("SUBTITLE_AI_MIN_CUE_DURATION", 0.5)
TARGET_CUE_DURATION_MIN = _positive_float("SUBTITLE_AI_TARGET_CUE_DURATION_MIN", 1.0)
TARGET_CUE_DURATION_MAX = _positive_float("SUBTITLE_AI_TARGET_CUE_DURATION_MAX", 6.0)
MAX_CUE_DURATION = _positive_float("SUBTITLE_AI_MAX_CUE_DURATION", 7.0)
MAX_CHARS_PER_LINE = _positive_int("SUBTITLE_AI_MAX_CHARS_PER_LINE", 42)
MAX_LINES = _positive_int("SUBTITLE_AI_MAX_LINES", 2)
MAX_CPS = _positive_float("SUBTITLE_AI_MAX_CPS", 21.0)
TARGET_CPS = _positive_float("SUBTITLE_AI_TARGET_CPS", 17.0)
