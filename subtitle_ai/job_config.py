"""The configuration a job actually ran with.

Behaviour is steered by environment variables and module defaults that can
change between deploys. A job row therefore carries a snapshot taken when
the worker starts it, so an old job can still be explained after the
configuration moved on ("which model, which thresholds, was name correction
on?"). Only an explicit allowlist is captured: credentials, hosts and paths
never are.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from typing import Any

SNAPSHOT_VERSION = 1

# Environment variables that change what a job produces.
BEHAVIOUR_ENV = (
    "SUBTITLE_AI_ASR_HOTWORDS", "SUBTITLE_AI_ASR_STYLE", "SUBTITLE_AI_REUSE_TRANSCRIPT_CACHE", "SUBTITLE_AI_CODE_SWITCH_DETECTION",
    "SUBTITLE_AI_COMPUTE_TYPE", "SUBTITLE_AI_DEDICATED_TRANSLATORS", "SUBTITLE_AI_GPU_SHARED", "SUBTITLE_AI_MODEL_IDLE_SECONDS",
    "SUBTITLE_AI_NAME_CORRECTION", "SUBTITLE_AI_NLLB_BACKEND", "SUBTITLE_AI_ORPHAN_CONTEXT_PADDING",
    "SUBTITLE_AI_TURN_DETECTION", "SUBTITLE_AI_VRAM_MARGIN_GB",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    return value


def capture(env: Mapping[str, str] | None = None) -> dict:
    """A JSON-serialisable description of the effective configuration."""
    import asr
    import subtitle_constraints as sc
    import translate

    env = os.environ if env is None else env
    return {
        "version": SNAPSHOT_VERSION,
        "pipeline_version": asr.PIPELINE_VERSION,
        "env": {name: env.get(name) or None for name in BEHAVIOUR_ENV},
        "remote_translation": bool(env.get("TRANSLATE_SERVER_URL")),
        "asr_defaults": {k: _jsonable(v) for k, v in dataclasses.asdict(asr.AsrConfig()).items()},
        "translation_defaults": {k: _jsonable(v)
                                 for k, v in dataclasses.asdict(translate.TranslationConfig()).items()},
        "subtitle_constraints": {
            "min_cue_duration": sc.MIN_CUE_DURATION, "max_cue_duration": sc.MAX_CUE_DURATION,
            "target_cue_duration_min": sc.TARGET_CUE_DURATION_MIN,
            "max_chars_per_line": sc.MAX_CHARS_PER_LINE, "max_lines": sc.MAX_LINES,
            "max_cps": sc.MAX_CPS, "target_cps": sc.TARGET_CPS,
        },
    }
