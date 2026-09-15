"""Input handling for the direct-subtitle-translation job type: read a user-supplied .srt
file and detect its spoken^H^H^H written language from the cue text — there is no audio
here, so this is the one place in the platform that legitimately runs text-based language
ID instead of Whisper's audio-based LID. Reuses `formatters/srt.py`'s parser (the same one
Stage 11 uses to re-validate its own output) rather than a second parser implementation.
"""
from __future__ import annotations

import logging

from app.pipeline.stage11_qc_output.formatters.srt import ParsedSrtCue, parse_srt

logger = logging.getLogger("subtitle_platform.pipeline.direct_translation.srt_io")


def read_srt_file(path: str) -> list[ParsedSrtCue]:
    # utf-8-sig transparently strips a leading BOM if present — real-world SRT files from
    # Windows subtitle editors very commonly carry one.
    with open(path, encoding="utf-8-sig") as f:
        content = f.read()
    cues = parse_srt(content)
    if not cues:
        raise ValueError("No cues could be parsed from the uploaded .srt file")
    return cues


def detect_source_language(cues: list[ParsedSrtCue]) -> str:
    from langdetect import LangDetectException, detect

    sample_text = " ".join(c.text for c in cues)[:5000]
    try:
        return detect(sample_text)
    except LangDetectException:
        logger.warning("langdetect could not determine a language for the uploaded SRT; defaulting to 'en'")
        return "en"
