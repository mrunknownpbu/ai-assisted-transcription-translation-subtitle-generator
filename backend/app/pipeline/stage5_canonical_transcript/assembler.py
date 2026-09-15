"""Stage 5: Canonical Transcript assembly.

Pure adapter stage: takes the winning ASRResult plus everything needed to fully explain
where it came from, and produces the CanonicalTranscript object every later stage reads.
No transcription or acoustic logic lives here — this stage only ever re-shapes and
annotates, so it stays trivially testable.
"""
from __future__ import annotations

import datetime as dt

from app.hardware import HardwareProfile
from app.pipeline.interfaces import ASRResult, CanonicalSegment, CanonicalTranscript, LanguageDetectionResult
from app.pipeline.stage4_asr.fallback import EngineAttempt


def build_canonical_transcript(
    asr_result: ASRResult,
    audio_stream_id: str,
    stream_hash: str,
    language_detection: LanguageDetectionResult,
    engine_attempts: list[EngineAttempt],
    hardware_profile: HardwareProfile | None = None,
) -> CanonicalTranscript:
    segments = [
        CanonicalSegment(
            segment_id=seg.segment_id,
            start=seg.start,
            end=seg.end,
            text=seg.text,
            normalized_text=seg.text,  # Stage 7 fills this in; identical to raw text until then
            words=list(seg.words),
            avg_confidence=seg.avg_confidence,
            no_speech_prob=seg.no_speech_prob,
            compression_ratio=seg.compression_ratio,
            avg_logprob=seg.avg_logprob,
        )
        for seg in asr_result.segments
    ]

    provenance = {
        "audio_stream_id": audio_stream_id,
        "stream_hash": stream_hash,
        "asr_engine": asr_result.engine,
        "asr_model_version": asr_result.model_version,
        "asr_engine_attempts": [attempt.__dict__ for attempt in engine_attempts],
        "language": asr_result.language,
        "language_detection_method": language_detection.method,
        "language_detection_confidence": language_detection.confidence,
        "language_detected": language_detection.detected_language,
        "language_effective": language_detection.effective_language,
        "language_overridden": language_detection.overridden,
        "hardware_profile": hardware_profile.as_dict() if hardware_profile else None,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    return CanonicalTranscript(segments=segments, provenance=provenance)
