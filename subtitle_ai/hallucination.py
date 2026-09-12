"""ASR hallucination detection.

Confirmed motivating defect (Season 01 audit, 2026-09-11): faster-whisper
large-v3 repeatedly inserted "Altyazı M.K." (a subtitle-credit artifact)
mid-sentence into real dialogue, across every one of 5 episodes checked --
and it propagated untranslated into the English output. A single string
substitution would have "fixed" that one phrase and caught nothing else.

Detection here combines two independent kinds of evidence, matching the
brief's requirement that legitimate speech survive and *new*, previously
unseen artifacts still have a chance of being caught:

1. Acoustic/decoder evidence (works on any artifact, known or not):
   no_speech_prob contradicting the presence of decoded words, an
   abnormally high compression ratio (degenerate/repetitive text -- the
   same signal faster-whisper's own compression_ratio_threshold uses),
   very low average log-probability, and exact-text recurrence at
   temporally unrelated points in the same episode (genuine repeated
   dialogue tends to be exact only in short, contextually-motivated runs;
   a phrase recurring across a large fraction of the episode's duration,
   verbatim, is far more consistent with a decoder fixation than with an
   actor saying the same line five separate times).
2. Known signatures (registry, not code): confirmed artifacts recorded in
   hallucination_signatures.json, each with an evidence trail. Adding a
   newly-confirmed artifact means adding a JSON entry, never touching this
   module or any ASR code.

A segment's final score is a combination of both; nothing here can, by
itself, silently discard genuine dialogue -- see SUPPRESSION_THRESHOLD's
docstring for how conservative the cutoff is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from transcript import Segment

_SIGNATURES_PATH = Path(__file__).parent / "hallucination_signatures.json"

# Faster-whisper's own default for flagging a decode as degenerate/looping.
COMPRESSION_RATIO_THRESHOLD = 2.4
NO_SPEECH_CONTRADICTION_THRESHOLD = 0.6   # decoder itself doubts speech is here
LOW_LOGPROB_THRESHOLD = -1.0

# A suppression this aggressive would risk real, quiet, or awkwardly-phrased
# dialogue -- the threshold is set high on purpose: only segments with
# strong, multi-signal evidence (a known signature match, or acoustic
# evidence *combined with* cross-episode recurrence) cross it. A single
# weak signal (e.g. only a slightly high compression ratio) flags for QC
# review but does not suppress.
SUPPRESSION_THRESHOLD = 0.75


@dataclass
class Signature:
    id: str
    language: str
    pattern: re.Pattern
    category: str
    weight: float
    description: str


def load_signatures(path: Path = _SIGNATURES_PATH) -> list[Signature]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Signature(id=s["id"], language=s["language"],
                      pattern=re.compile(s["pattern"], re.IGNORECASE),
                      category=s["category"], weight=s["weight"], description=s["description"])
           for s in data["signatures"]]


@dataclass
class HallucinationFinding:
    segment_index: int
    score: float
    reasons: list[str] = field(default_factory=list)
    suppress: bool = False


def _recurrence_score(segment: Segment, all_segments: list[Segment], episode_duration: float) -> float:
    """How consistent this segment's exact text is with a hallucinated
    fixation vs. genuine repeated dialogue. Counts verbatim (casefolded,
    whitespace-normalized) matches elsewhere in the episode and how widely
    they're spread relative to the episode's own length -- a phrase said
    twice in the same scene 4 seconds apart is normal dialogue; the same
    phrase recurring across a third or more of the episode's runtime, with
    no dramatic reason to repeat, is the shape a decoder fixation takes.

    Single-word text is deliberately exempted (`>= 2 words` gate below):
    common acknowledgements ("Evet.", "Tamam.", "Hayır.") are expected to
    recur dozens of times across any episode of ordinary dialogue -- real
    measured case, 15 occurrences of "Evet." in one real episode -- and
    are never what this heuristic is meant to catch."""
    text = re.sub(r"\s+", " ", segment.text.strip().casefold())
    if len(text.split()) < 2:
        return 0.0
    matches = [s for s in all_segments if s.index != segment.index
              and re.sub(r"\s+", " ", s.text.strip().casefold()) == text]
    if len(matches) < 2:
        return 0.0
    spread = max(s.start for s in matches + [segment]) - min(s.start for s in matches + [segment])
    spread_ratio = spread / episode_duration if episode_duration > 0 else 0.0
    count_signal = min(1.0, len(matches) / 4)
    spread_signal = min(1.0, spread_ratio / 0.3)   # spread across >=30% of runtime maxes this out
    return min(1.0, 0.5 * count_signal + 0.5 * spread_signal)


def score_segment(segment: Segment, all_segments: list[Segment], episode_duration: float,
                  signatures: list[Signature], language: str) -> HallucinationFinding:
    reasons: list[str] = []
    score = 0.0

    for sig in signatures:
        if sig.language != language:
            continue
        if sig.pattern.search(segment.text):
            reasons.append(f"signature:{sig.id}")
            score = max(score, sig.weight)

    if segment.no_speech_prob >= NO_SPEECH_CONTRADICTION_THRESHOLD and segment.text.strip():
        reasons.append(f"no_speech_prob={segment.no_speech_prob:.2f} with decoded text present")
        score = max(score, 0.4)

    if segment.compression_ratio >= COMPRESSION_RATIO_THRESHOLD:
        reasons.append(f"compression_ratio={segment.compression_ratio:.2f} (degenerate/repetitive text)")
        score = max(score, 0.5)

    if segment.avg_logprob <= LOW_LOGPROB_THRESHOLD:
        reasons.append(f"avg_logprob={segment.avg_logprob:.2f} (low decoder confidence)")
        score = max(score, 0.3)

    recurrence = _recurrence_score(segment, all_segments, episode_duration)
    if recurrence > 0:
        reasons.append(f"recurrence_score={recurrence:.2f} (verbatim text recurs widely across the episode)")
        # Recurrence compounds with any other signal rather than just
        # taking the max -- a segment that recurs widely AND has a low
        # no_speech_prob-based signal is more suspect than either alone.
        score = min(1.0, score + 0.3 * recurrence) if score > 0 else recurrence * 0.6

    # bool(), not a bare comparison: faster-whisper's segment/word timing
    # fields are numpy.float64, so recurrence (derived from segment.start/
    # end arithmetic -- see _recurrence_score) can itself be numpy.float64,
    # making `score` numpy-typed too in that branch. `score >= threshold`
    # then yields numpy.bool_, which json.dumps() (jobstore persisting QC)
    # rejects -- a real crash hit on a real episode with recurring dialogue.
    suppress = bool(score >= SUPPRESSION_THRESHOLD)
    return HallucinationFinding(segment_index=segment.index, score=round(score, 3),
                                reasons=reasons, suppress=suppress)


def detect(segments: list[Segment], language: str,
          signatures: list[Signature] | None = None) -> list[HallucinationFinding]:
    """Score every segment; mutate each Segment's hallucination_score/
    hallucination_reasons/suppressed in place (so the canonical transcript
    itself carries the finding -- see transcript.Segment) and return the
    findings list for the QC layer."""
    if signatures is None:
        signatures = load_signatures()
    episode_duration = max((s.end for s in segments), default=0.0)
    findings = []
    for seg in segments:
        finding = score_segment(seg, segments, episode_duration, signatures, language)
        seg.hallucination_score = finding.score
        seg.hallucination_reasons = finding.reasons
        seg.suppressed = finding.suppress
        findings.append(finding)
    return findings
