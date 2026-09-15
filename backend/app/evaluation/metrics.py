"""Translation-accuracy scoring for the evaluation harness: compares our pipeline's
translated output against a real reference subtitle track (an embedded track or sidecar
file already present in the library) for the SAME episode. This measures and helps tune
the existing pipeline (glossary, chunking, hallucination thresholds) -- it never changes
any model weights.

No external NLP dependency (no sacrebleu/nltk): chrF (character n-gram F-score) needs only
stdlib, works directly on raw text without a language-specific tokenizer, and is more
forgiving of the punctuation/casing drift that's routine between two independently-produced
subtitle tracks of the same dialogue than exact-word metrics like BLEU would be.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.pipeline.stage11_qc_output.formatters.srt import ParsedSrtCue

# Strips SDH/hearing-impaired-only markup that a reference track carries but a plain
# translation never would (sound descriptions, speaker-name captions) -- comparing through
# these verbatim would only measure whose SDH conventions differ, not translation quality.
_BRACKETED_SOUND_RE = re.compile(r"[\[(][^\])]*[\])]")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")  # <i>, </i>, <b>, <font color=...>, etc. -- real subtitle styling markup
_SPEAKER_LABEL_RE = re.compile(r"(?:^|(?<=\s))[A-Z][A-Z0-9 '.-]{1,30}:\s*")
_LEADING_DASH_RE = re.compile(r"(?:^|(?<=\s))-\s*")
_PUNCT_RE = re.compile(r"[^\w\s']", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_for_comparison(text: str) -> str:
    text = _HTML_TAG_RE.sub(" ", text)
    text = _BRACKETED_SOUND_RE.sub(" ", text)
    # Two-speaker exchanges are routinely joined into one cue/line ("- Hello? - Hi."), so
    # dash- and speaker-label-stripping must match mid-string too, not just a line's start.
    text = _SPEAKER_LABEL_RE.sub("", _LEADING_DASH_RE.sub("", text))
    text = _PUNCT_RE.sub(" ", text).lower()
    return _WS_RE.sub(" ", text).strip()


def _char_ngrams(text: str, n: int) -> Counter:
    # chrF operates over the whole string including internal spaces (not per-word), which
    # is what lets it reward correctly-placed word boundaries without needing tokenization.
    if len(text) < n:
        return Counter()
    return Counter(text[i:i + n] for i in range(len(text) - n + 1))


def chrf_score(hypothesis: str, reference: str, max_n: int = 6, beta: float = 2.0) -> float:
    """Standard chrF: F-beta over character n-gram precision/recall, averaged across
    n=1..max_n. beta=2 (the original chrF paper's default) weights recall twice precision,
    appropriate here since a dropped clause is a worse translation defect than an
    over-literal extra word."""
    hyp, ref = normalize_for_comparison(hypothesis), normalize_for_comparison(reference)
    if not hyp and not ref:
        return 1.0
    if not hyp or not ref:
        return 0.0

    f_scores = []
    for n in range(1, max_n + 1):
        hyp_grams, ref_grams = _char_ngrams(hyp, n), _char_ngrams(ref, n)
        if not hyp_grams or not ref_grams:
            continue
        overlap = sum((hyp_grams & ref_grams).values())
        precision = overlap / sum(hyp_grams.values())
        recall = overlap / sum(ref_grams.values())
        if precision + recall == 0:
            f_scores.append(0.0)
            continue
        beta_sq = beta * beta
        f_scores.append((1 + beta_sq) * precision * recall / (beta_sq * precision + recall))
    return sum(f_scores) / len(f_scores) if f_scores else 0.0


def word_overlap_f1(hypothesis: str, reference: str) -> float:
    """Simpler, more human-legible companion metric to chrF: plain bag-of-words F1. Useful
    as a sanity cross-check since it's easy to reason about (e.g. "half the reference's
    words are missing") in a way a character n-gram score isn't."""
    hyp_words = normalize_for_comparison(hypothesis).split()
    ref_words = normalize_for_comparison(reference).split()
    if not hyp_words and not ref_words:
        return 1.0
    if not hyp_words or not ref_words:
        return 0.0
    hyp_counts, ref_counts = Counter(hyp_words), Counter(ref_words)
    overlap = sum((hyp_counts & ref_counts).values())
    precision = overlap / len(hyp_words)
    recall = overlap / len(ref_words)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


@dataclass(frozen=True)
class CueComparison:
    hypothesis_index: int
    start: float
    end: float
    hypothesis_text: str
    reference_text: str  # "" when no reference cue overlaps this hypothesis cue's time range
    chrf: float
    word_f1: float


@dataclass(frozen=True)
class EvaluationReport:
    cue_count: int
    matched_count: int  # hypothesis cues that had at least one overlapping reference cue
    coverage: float  # matched_count / cue_count
    mean_chrf: float  # over matched cues only -- an unmatched cue has no meaningful score
    mean_word_f1: float
    comparisons: list[CueComparison]


def _overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def align_reference_text(hypothesis_cue: ParsedSrtCue, reference_cues: list[ParsedSrtCue]) -> str:
    """Concatenates every reference cue that overlaps this hypothesis cue's time range, in
    time order. Cue boundaries from our own N:M segmentation routinely don't line up 1:1
    with an independently-authored reference track's boundaries, so alignment has to be by
    time overlap rather than by cue index."""
    overlapping = [
        r for r in reference_cues if _overlap_seconds(hypothesis_cue.start, hypothesis_cue.end, r.start, r.end) > 0
    ]
    overlapping.sort(key=lambda r: r.start)
    return " ".join(r.text.replace("\n", " ") for r in overlapping)


def evaluate_cues(hypothesis_cues: list[ParsedSrtCue], reference_cues: list[ParsedSrtCue]) -> EvaluationReport:
    comparisons = []
    for h in hypothesis_cues:
        raw_ref_text = align_reference_text(h, reference_cues)
        hyp_text = h.text.replace("\n", " ")
        # A reference cue that is purely on-screen captions or sound description (entirely
        # bracketed, e.g. "[Hengshi Air] [Three years later]") normalizes to nothing.
        # normalize_for_comparison's SDH-stripping is correct to remove that -- but scoring
        # it as-is would then compare real audio-derived output against literally no text,
        # forcing a hard 0.0 for a cue our audio-only pipeline could never have matched
        # (nobody says an on-screen caption out loud). Treated the same as no overlapping
        # reference cue at all, confirmed via a real case: a genuinely hallucinated cue
        # ("Subtitles group") and a correct one both landed on pure-caption reference text
        # and scored identically 0.00, which is a measurement artifact, not evidence either
        # is a mistranslation.
        ref_text = raw_ref_text if normalize_for_comparison(raw_ref_text) else ""
        chrf = chrf_score(hyp_text, ref_text) if ref_text else 0.0
        word_f1 = word_overlap_f1(hyp_text, ref_text) if ref_text else 0.0
        comparisons.append(CueComparison(
            hypothesis_index=h.index, start=h.start, end=h.end,
            hypothesis_text=hyp_text, reference_text=ref_text, chrf=chrf, word_f1=word_f1,
        ))

    matched = [c for c in comparisons if c.reference_text]
    cue_count = len(comparisons)
    matched_count = len(matched)
    return EvaluationReport(
        cue_count=cue_count,
        matched_count=matched_count,
        coverage=(matched_count / cue_count) if cue_count else 0.0,
        mean_chrf=(sum(c.chrf for c in matched) / matched_count) if matched_count else 0.0,
        mean_word_f1=(sum(c.word_f1 for c in matched) / matched_count) if matched_count else 0.0,
        comparisons=comparisons,
    )
