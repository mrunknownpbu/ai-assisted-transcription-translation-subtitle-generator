import pytest

from app.pipeline.interfaces import ASRWord, CanonicalSegment, CanonicalTranscript, VoiceActivitySpan
from app.pipeline.stage6_hallucination_defense.defense import (
    HallucinationDefenseConfig, apply_hallucination_defense,
)

DEFAULT_CONFIG = HallucinationDefenseConfig()


def _seg(segment_id, start, end, words, avg_confidence=0.9, no_speech_prob=None,
         compression_ratio=None, avg_logprob=None):
    text = " ".join(w.word for w in words)
    return CanonicalSegment(segment_id=segment_id, start=start, end=end, text=text, normalized_text=text,
                             words=words, avg_confidence=avg_confidence, no_speech_prob=no_speech_prob,
                             compression_ratio=compression_ratio, avg_logprob=avg_logprob)


def _words(text, start=0.0, step=0.3, confidence=0.9):
    words = []
    t = start
    for w in text.split():
        words.append(ASRWord(w, t, t + step, confidence))
        t += step
    return words


@pytest.mark.unit
def test_repetition_loop_suppresses_alone_via_high_weight():
    loop_words = []
    t = 0.0
    for _ in range(6):
        for w in ("thank", "you"):
            loop_words.append(ASRWord(w, t, t + 0.2, 0.99))
            t += 0.2
    seg = _seg("seg_loop", 0.0, t, loop_words, avg_confidence=0.99)
    transcript = CanonicalTranscript(segments=[seg], provenance={})

    decisions = apply_hallucination_defense(transcript, vad_spans=[], config=DEFAULT_CONFIG, language="en")

    assert seg.suppressed is True
    assert seg.hallucination_score == pytest.approx(0.9)
    assert decisions[0].decision == "suppressed"
    assert "repeated" in decisions[0].reason


@pytest.mark.unit
def test_signature_match_suppresses_alone():
    # "altyazı" is a bundled real signature for Turkish (see default_signatures.json).
    words = _words("bu altyazı ekibi tarafından hazırlanmıştır")
    seg = _seg("seg_sig", 0.0, 1.5, words, avg_confidence=0.95)
    transcript = CanonicalTranscript(segments=[seg], provenance={})

    decisions = apply_hallucination_defense(transcript, vad_spans=[], config=DEFAULT_CONFIG, language="tr")

    assert seg.suppressed is True
    assert seg.hallucination_score == pytest.approx(0.9)
    assert "signature" in decisions[0].reason


@pytest.mark.unit
def test_signature_is_gated_by_language():
    words = _words("bu altyazı ekibi tarafından hazırlanmıştır")
    seg = _seg("seg_sig_wronglang", 0.0, 1.5, words, avg_confidence=0.95)
    transcript = CanonicalTranscript(segments=[seg], provenance={})

    decisions = apply_hallucination_defense(transcript, vad_spans=[], config=DEFAULT_CONFIG, language="en")

    assert seg.suppressed is False
    assert seg.hallucination_score == 0.0
    assert decisions == []  # zero-score segments are not logged at all


@pytest.mark.unit
def test_single_acoustic_signal_alone_is_kept_marginal_not_suppressed():
    """A false-negative-minimization property carried over from the proven design: no
    single acoustic signal (compression ratio, no_speech_prob, avg_logprob, or this
    platform's own VAD+confidence check) suppresses by itself — only a signature match,
    a repetition loop, or several signals reinforcing each other via recurrence do."""
    words = _words("some perfectly ordinary sentence")
    seg = _seg("seg_acoustic", 0.0, 1.5, words, avg_confidence=0.9, compression_ratio=3.0)
    transcript = CanonicalTranscript(segments=[seg], provenance={})

    decisions = apply_hallucination_defense(transcript, vad_spans=[], config=DEFAULT_CONFIG, language="en")

    assert seg.suppressed is False
    assert seg.hallucination_score == pytest.approx(0.5)
    assert decisions[0].decision == "kept_marginal"


@pytest.mark.unit
def test_low_confidence_and_no_vad_activity_alone_is_kept_marginal():
    words = [ASRWord("mumble", 5.0, 5.6, 0.10), ASRWord("something", 5.6, 6.2, 0.12)]
    seg = _seg("seg_vad", 5.0, 6.2, words, avg_confidence=0.11)
    transcript = CanonicalTranscript(segments=[seg], provenance={})

    decisions = apply_hallucination_defense(transcript, vad_spans=[], config=DEFAULT_CONFIG, language="en")

    assert seg.suppressed is False
    assert seg.hallucination_score == pytest.approx(0.6)
    assert decisions[0].decision == "kept_marginal"


@pytest.mark.unit
def test_real_voice_activity_prevents_the_confidence_vad_signal_from_firing():
    words = [ASRWord("quiet", 5.0, 5.6, 0.10), ASRWord("speech", 5.6, 6.2, 0.12)]
    seg = _seg("seg_real_voice", 5.0, 6.2, words, avg_confidence=0.11)
    transcript = CanonicalTranscript(segments=[seg], provenance={})
    vad_spans = [VoiceActivitySpan(start=4.8, end=6.5)]

    decisions = apply_hallucination_defense(transcript, vad_spans=vad_spans, config=DEFAULT_CONFIG, language="en")

    assert seg.hallucination_score == 0.0
    assert decisions == []


@pytest.mark.unit
def test_recurrence_bonus_pushes_a_moderate_signal_over_the_suppression_threshold():
    """Five near-identical segments each carrying a moderate acoustic signal (0.5):
    recurrence saturates at the default count (5) and adds its full bonus, crossing the
    0.75 suppression threshold even though no single segment would suppress alone."""
    segments = []
    for i in range(5):
        words = _words("phantom hallucinated line", start=float(i) * 10)
        segments.append(_seg(f"seg_{i}", float(i) * 10, float(i) * 10 + 1.0, words,
                              avg_confidence=0.9, compression_ratio=3.0))
    transcript = CanonicalTranscript(segments=segments, provenance={})

    decisions = apply_hallucination_defense(transcript, vad_spans=[], config=DEFAULT_CONFIG, language="en")

    assert all(s.suppressed for s in segments)
    assert all(d.decision == "suppressed" for d in decisions)
    assert segments[0].hallucination_score == pytest.approx(0.8)


@pytest.mark.unit
def test_pure_recurrence_without_any_other_signal_never_suppresses_alone():
    """Legitimate repeated dialogue (e.g. a line repeated for emphasis) with no other
    hallucination signal must never be auto-suppressed just for recurring — recurrence is
    only ever a bonus on top of an existing signal, per the false-negative-minimization
    requirement."""
    segments = []
    for i in range(6):
        words = _words("okay okay lets go", start=float(i) * 10)
        segments.append(_seg(f"seg_{i}", float(i) * 10, float(i) * 10 + 1.0, words, avg_confidence=0.9))
    transcript = CanonicalTranscript(segments=segments, provenance={})

    decisions = apply_hallucination_defense(transcript, vad_spans=[], config=DEFAULT_CONFIG, language="en")

    assert all(not s.suppressed for s in segments)
    assert all(d.decision == "kept_marginal" for d in decisions)


@pytest.mark.unit
def test_false_negative_minimization_across_a_mixed_batch():
    confident = _seg("seg_confident", 0.0, 1.0, _words("real dialogue here"), avg_confidence=0.9)
    marginal = _seg("seg_marginal", 2.0, 3.0, [ASRWord("quiet", 2.0, 2.5, 0.13), ASRWord("speech", 2.5, 3.0, 0.14)],
                     avg_confidence=0.135)
    loop_words = []
    t = 4.0
    for _ in range(6):
        for w in ("no", "no"):
            loop_words.append(ASRWord(w, t, t + 0.15, 0.95))
            t += 0.15
    looped = _seg("seg_loop", 4.0, t, loop_words, avg_confidence=0.95)
    transcript = CanonicalTranscript(segments=[confident, marginal, looped], provenance={})
    vad_spans = [VoiceActivitySpan(start=0.0, end=1.0)]  # covers only the confident segment

    apply_hallucination_defense(transcript, vad_spans=vad_spans, config=DEFAULT_CONFIG, language="en")

    assert confident.suppressed is False
    assert marginal.suppressed is False  # single weak signal, kept per false-negative minimization
    assert looped.suppressed is True     # repetition loop is high-confidence evidence


@pytest.mark.unit
def test_nothing_is_ever_deleted_only_flagged():
    loop_words = []
    t = 0.0
    for _ in range(6):
        for w in ("phantom", "text"):
            loop_words.append(ASRWord(w, t, t + 0.2, 0.9))
            t += 0.2
    seg = _seg("seg_x", 0.0, t, loop_words, avg_confidence=0.9)
    transcript = CanonicalTranscript(segments=[seg], provenance={})

    apply_hallucination_defense(transcript, vad_spans=[], config=DEFAULT_CONFIG, language="en")

    assert "phantom" in transcript.segments[0].text
    assert transcript.segments[0].words[0].word == "phantom"
    assert transcript.segments[0].suppressed is True


@pytest.mark.unit
def test_every_decision_carries_the_threshold_config_used():
    words = _words("some ordinary text", confidence=0.05)
    seg = _seg("seg_y", 0.0, 1.0, words, avg_confidence=0.05)
    transcript = CanonicalTranscript(segments=[seg], provenance={})

    decisions = apply_hallucination_defense(transcript, vad_spans=[], config=DEFAULT_CONFIG, language="en")

    assert decisions[0].threshold["suppression_score_threshold"] == DEFAULT_CONFIG.suppression_score_threshold
    assert decisions[0].threshold["compression_ratio_threshold"] == DEFAULT_CONFIG.compression_ratio_threshold
