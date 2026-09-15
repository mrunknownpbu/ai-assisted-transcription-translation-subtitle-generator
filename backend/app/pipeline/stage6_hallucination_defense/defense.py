"""Stage 6: Hallucination Defense.

Every segment gets a single weighted **score in [0, 1]** built from several independent
signals via `max()` (any one strong signal is enough to raise suspicion) plus an additive
recurrence bonus (repeated hallucinations reinforce each other). This replaces an earlier
AND-gate design with a scoring approach proven in production on real broadcast content
(ported from a sibling project's `hallucination.py`, itself shaped by specific documented
incidents — see `default_signatures.json`), while keeping this platform's own Silero VAD
signal, which neither sibling project has.

Signals (each sets a score *floor* via `max()`, so they combine rather than requiring all
of them):

1. **Known-artifact signature match** — a regex registry (`signatures.py`) of specific text
   patterns a model has been observed to hallucinate for a given language (e.g. a Turkish
   subtitle-credit line injected into audio that never said it). Weight per signature.
2. **No-speech contradiction** — the ASR engine's own `no_speech_prob` says it doubted
   speech was even present here, yet it emitted text anyway.
3. **Degenerate compression ratio** — `gzip(text)` compresses unusually well, the classic
   signature of a decoder stuck in a repetitive/degenerate loop.
4. **Low average log-probability** — the engine's own per-token confidence was poor
   throughout the segment.
5. **Low word confidence + no VAD voice activity** — this platform's own acoustic check:
   quiet real speech has low confidence but IS real, and loud non-speech noise can trip VAD
   without hallucinated text, so this signal only fires when both agree.
6. **Within-segment repetition loop** — the same short phrase repeated many times
   consecutively, largely independent of confidence (loops are often reported with
   deceptively high per-token confidence).
7. **Cross-segment recurrence** (additive bonus, not a floor) — near-identical text
   recurring verbatim across many segments in the same job is itself suspicious, especially
   layered on top of any other signal above.

Suppression happens only at `score >= suppression_score_threshold` (default 0.75,
deliberately high). Nothing is ever deleted: a suppressed segment keeps its original text
and word timestamps (`suppressed=True`, `suppression_reason` set, `hallucination_score`
recorded) purely so the decision is reversible and auditable. A segment scoring `>0` but
below the suppression threshold is logged as `kept_marginal`, never silently dropped — this
is the false-negative-minimization bias the system is required to have.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.pipeline.interfaces import ASRWord, CanonicalSegment, CanonicalTranscript, SuppressionDecision, VoiceActivitySpan
from app.pipeline.stage6_hallucination_defense.signatures import Signature, load_signatures, matching_weight

logger = logging.getLogger("subtitle_platform.pipeline.hallucination_defense")


@dataclass(frozen=True)
class HallucinationDefenseConfig:
    min_word_confidence: float = 0.15
    repetition_loop_min_repeats: int = 4
    repetition_loop_min_phrase_words: int = 2
    compression_ratio_threshold: float = 2.4
    no_speech_prob_threshold: float = 0.6
    low_avg_logprob_threshold: float = -1.0
    suppression_score_threshold: float = 0.75
    recurrence_bonus_weight: float = 0.3
    recurrence_base_weight: float = 0.6
    recurrence_saturation_count: int = 5  # occurrence count at which the recurrence signal saturates at 1.0
    signatures_path: str | None = None  # None => bundled default_signatures.json

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def compute_voice_activity_spans(pcm_16k_mono, threshold: float = 0.5) -> list[VoiceActivitySpan]:
    """Uses the Silero VAD ONNX model bundled with faster-whisper (the same model family
    referenced in the architecture plan) rather than adding a second, separately
    maintained VAD dependency."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(threshold=threshold)
    raw_spans = get_speech_timestamps(pcm_16k_mono, options)
    sample_rate = 16000
    return [VoiceActivitySpan(start=s["start"] / sample_rate, end=s["end"] / sample_rate) for s in raw_spans]


def _overlaps_voice_activity(start: float, end: float, spans: list[VoiceActivitySpan]) -> bool:
    return any(not (end <= s.start or start >= s.end) for s in spans)


def _clean_token(word: str) -> str:
    return word.lower().strip(" .,!?;:\"'-")


def _detect_repetition_loop(words: list[ASRWord], min_repeats: int, min_phrase_words: int) -> tuple[bool, str | None]:
    tokens = [_clean_token(w.word) for w in words if _clean_token(w.word)]
    n = len(tokens)
    max_phrase_len = max(min_phrase_words, min(6, n // max(min_repeats, 1)))

    for phrase_len in range(min_phrase_words, max_phrase_len + 1):
        i = 0
        while i + phrase_len * min_repeats <= n:
            phrase = tokens[i : i + phrase_len]
            repeats = 1
            j = i + phrase_len
            while j + phrase_len <= n and tokens[j : j + phrase_len] == phrase:
                repeats += 1
                j += phrase_len
            if repeats >= min_repeats:
                return True, " ".join(phrase)
            i += 1
    return False, None


def _normalize_for_recurrence(text: str) -> str:
    return " ".join(text.lower().split())


def _recurrence_score(
    segment: CanonicalSegment, all_segments: list[CanonicalSegment], saturation_count: int,
) -> tuple[float, int]:
    normalized = _normalize_for_recurrence(segment.text)
    if len(normalized.split()) < 2:
        return 0.0, 0
    occurrences = sum(1 for s in all_segments if _normalize_for_recurrence(s.text) == normalized)
    if occurrences < 2:
        return 0.0, occurrences
    score = min(1.0, (occurrences - 1) / max(saturation_count - 1, 1))
    return score, occurrences


def _score_segment(
    segment: CanonicalSegment, all_segments: list[CanonicalSegment], language: str,
    signatures: list[Signature], vad_spans: list[VoiceActivitySpan], config: HallucinationDefenseConfig,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    sig_weight, sig_id = matching_weight(segment.text, language, signatures)
    if sig_weight > 0:
        score = max(score, sig_weight)
        reasons.append(f"known-artifact signature '{sig_id}' matched (weight={sig_weight})")

    if segment.no_speech_prob is not None and segment.no_speech_prob >= config.no_speech_prob_threshold:
        score = max(score, 0.4)
        reasons.append(f"no_speech_prob={segment.no_speech_prob:.2f} >= {config.no_speech_prob_threshold}")

    if segment.compression_ratio is not None and segment.compression_ratio >= config.compression_ratio_threshold:
        score = max(score, 0.5)
        reasons.append(f"compression_ratio={segment.compression_ratio:.2f} >= {config.compression_ratio_threshold}")

    if segment.avg_logprob is not None and segment.avg_logprob <= config.low_avg_logprob_threshold:
        score = max(score, 0.3)
        reasons.append(f"avg_logprob={segment.avg_logprob:.2f} <= {config.low_avg_logprob_threshold}")

    has_voice = _overlaps_voice_activity(segment.start, segment.end, vad_spans)
    if segment.avg_confidence < config.min_word_confidence and not has_voice:
        score = max(score, 0.6)
        reasons.append(
            f"avg_confidence={segment.avg_confidence:.3f} < {config.min_word_confidence} "
            f"and no VAD voice activity in [{segment.start:.2f},{segment.end:.2f}]"
        )

    loop_found, phrase = _detect_repetition_loop(
        segment.words, config.repetition_loop_min_repeats, config.repetition_loop_min_phrase_words,
    )
    if loop_found:
        score = max(score, 0.9)
        reasons.append(f"phrase '{phrase}' repeated >= {config.repetition_loop_min_repeats} times consecutively")

    recurrence, occurrences = _recurrence_score(segment, all_segments, config.recurrence_saturation_count)
    if recurrence > 0:
        if score > 0:
            score = min(1.0, score + config.recurrence_bonus_weight * recurrence)
        else:
            score = recurrence * config.recurrence_base_weight
        reasons.append(f"near-identical text recurs {occurrences}x across the transcript")

    return score, reasons


def apply_hallucination_defense(
    transcript: CanonicalTranscript, vad_spans: list[VoiceActivitySpan], config: HallucinationDefenseConfig,
    language: str,
) -> list[SuppressionDecision]:
    signatures = load_signatures(config.signatures_path)
    all_segments = transcript.segments
    threshold_dict = config.as_dict()
    decisions: list[SuppressionDecision] = []

    for segment in all_segments:
        score, reasons = _score_segment(segment, all_segments, language, signatures, vad_spans, config)
        segment.hallucination_score = score
        if score <= 0.0:
            continue

        reason_text = "; ".join(reasons)
        if score >= config.suppression_score_threshold:
            segment.suppressed = True
            segment.suppression_reason = reason_text
            decisions.append(SuppressionDecision(
                segment_id=segment.segment_id, decision="suppressed",
                reason=f"score={score:.2f} >= {config.suppression_score_threshold}: {reason_text}",
                method="weighted_score", threshold=threshold_dict,
            ))
        else:
            decisions.append(SuppressionDecision(
                segment_id=segment.segment_id, decision="kept_marginal",
                reason=f"score={score:.2f} < {config.suppression_score_threshold} — kept to minimize false negatives: {reason_text}",
                method="weighted_score", threshold=threshold_dict,
            ))

    suppressed_count = sum(1 for d in decisions if d.decision == "suppressed")
    logger.info("Hallucination defense: %d segment(s) suppressed, %d flagged kept_marginal, out of %d total",
                suppressed_count, len(decisions) - suppressed_count, len(all_segments))
    return decisions
