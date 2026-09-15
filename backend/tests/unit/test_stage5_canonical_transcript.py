import pytest

from app.hardware import AcceleratorVendor, HardwareProfile
from app.pipeline.interfaces import ASRResult, ASRSegment, ASRWord, LanguageDetectionResult
from app.pipeline.stage4_asr.fallback import EngineAttempt
from app.pipeline.stage5_canonical_transcript.assembler import build_canonical_transcript


def _asr_result():
    return ASRResult(
        engine="faster_whisper", model_version="faster-whisper:medium", language="en",
        segments=[ASRSegment(segment_id="seg_00000", start=0.0, end=1.5, text="Hello there",
                              words=[ASRWord("Hello", 0.0, 0.6, 0.95), ASRWord("there", 0.6, 1.5, 0.92)],
                              avg_confidence=0.935)],
    )


@pytest.mark.unit
def test_canonical_transcript_preserves_words_and_records_full_provenance():
    lang = LanguageDetectionResult(detected_language="en", confidence=0.9, method="faster_whisper_lid")
    attempts = [EngineAttempt(engine="faster_whisper", outcome="succeeded")]
    profile = HardwareProfile(vendor=AcceleratorVendor.CPU, gpu_count=0, cpu_cores=8, total_ram_mb=16384)

    transcript = build_canonical_transcript(
        _asr_result(), audio_stream_id="stream-1", stream_hash="hash-1",
        language_detection=lang, engine_attempts=attempts, hardware_profile=profile,
    )

    assert len(transcript.segments) == 1
    seg = transcript.segments[0]
    assert seg.text == "Hello there"
    assert seg.normalized_text == "Hello there"  # untouched until Stage 7 runs
    assert len(seg.words) == 2
    assert seg.suppressed is False

    prov = transcript.provenance
    assert prov["audio_stream_id"] == "stream-1"
    assert prov["stream_hash"] == "hash-1"
    assert prov["asr_engine"] == "faster_whisper"
    assert prov["asr_engine_attempts"] == [{"engine": "faster_whisper", "outcome": "succeeded", "detail": None}]
    assert prov["language_detection_method"] == "faster_whisper_lid"
    assert prov["hardware_profile"]["vendor"] == "cpu"
    assert "generated_at" in prov


@pytest.mark.unit
def test_canonical_transcript_handles_missing_hardware_profile():
    lang = LanguageDetectionResult(detected_language="en", confidence=0.9, method="fake")
    transcript = build_canonical_transcript(
        _asr_result(), audio_stream_id="s", stream_hash="h", language_detection=lang, engine_attempts=[],
    )
    assert transcript.provenance["hardware_profile"] is None
