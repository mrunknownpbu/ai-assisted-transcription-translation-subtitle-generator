"""Re-time an existing subtitle against the audio's own word timings.

Input: the cues of a human subtitle whose TEXT is right but whose TIMES are
off, and the timed words of an audio-only transcript of the same video.
Output: the same cues, same text, new times.

Boundaries (see docs/decisions/2026-10-03-subtitle-retiming.md):

* The transcript is made from audio alone, exactly as Workflow A makes it. The
  subtitle is never ASR input and never repairs the transcript; it is only
  compared with it. The subtitle's text is never changed.
* The correction is not a constant. It is estimated from the audio for this one
  file as a piecewise-linear function of time (a shift, a frame-rate stretch, or
  steps where a cut or an ad break differs), and the function is reported with the
  evidence behind it. With too little evidence nothing is written.

Pure logic: no GPU, no files, no models.
"""

from __future__ import annotations

import difflib
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

# Languages written without spaces between words: compared per character.
UNSPACED = frozenset({"ja", "zh", "th"})

# A run of this many identical tokens shared by subtitle and transcript is an
# anchor. Three words match what scripts/eval_srt_quality.py uses to measure
# offsets (MIN_ANCHOR_WORDS); unspaced languages need more characters because a
# character is far less distinctive than a word.
MIN_RUN_WORDS = 3
MIN_RUN_CHARS = 6

# Tokens a cue must contribute to count as anchored (one lone match is noise).
MIN_CUE_TOKENS = 2

# A point is an outlier when its offset disagrees with the median of the points
# before it AND the median of the points after it by more than this (seconds).
OUTLIER_SECONDS = 1.0
NEIGHBOURS = 4

# A segment of the timeline is "one function" when 90% of its anchors lie within
# this of the fitted line. Measured 2026-10-03 on Love Is In The Air S01E01-E04
# (a human subtitle shifted by a known amount, against the production
# transcript): the 90th-percentile residual of a correct single line is
# 0.38-0.44 s, so 0.7 s is 1.6x the worst. At 0.4 s noise alone split segments.
FIT_TOLERANCE = 0.7

# Smallest step between two stretches of the timeline worth a split, and the
# smallest drift over a segment worth a slope instead of a constant.
MIN_STEP = 0.4
INLIER_BAND = 1.0
MIN_DRIFT = 0.3

MIN_SEGMENT_POINTS = 8

# Evidence required before any time is changed.
MIN_ANCHORED_CUES = 20
MIN_ANCHORED_FRACTION = 0.25
# After fitting, 95% of anchors must agree with the function within this.
MAX_RESIDUAL_P95 = 1.0

# More outliers than this and the subtitle is not following the audio in order.
MAX_OUTLIER_FRACTION = 0.2

# A fitted function this close to zero everywhere means the subtitle already
# lines up with the audio (the measured noise floor is 0.04-0.10 s).
ALIGNED_WITHIN = 0.25

MAX_ABS_SLOPE = 0.1   # seconds of offset per second; a 25 vs 23.976 fps mismatch is 0.043


class RetimeRefused(Exception):
    """Not enough evidence to retime this subtitle against this audio."""


@dataclass
class TimedWord:
    text: str
    start: float
    end: float


@dataclass
class Anchor:
    time: float      # in the subtitle's own timeline (cue-interpolated)
    offset: float    # transcript time minus subtitle time
    cue: int


@dataclass
class Piece:
    t0: float        # subtitle-timeline span of the evidence
    t1: float
    offset0: float   # offset at t0
    slope: float     # change of offset per second

    def offset_at(self, t: float) -> float:
        t = min(max(t, self.t0), self.t1)
        return self.offset0 + self.slope * (t - self.t0)


@dataclass
class RetimeReport:
    cues: int
    anchored_cues: int
    method: str = ""
    pieces: list[Piece] = field(default_factory=list)
    residual_p50: float = 0.0
    residual_p95: float = 0.0
    max_offset: float = 0.0

    @property
    def changed(self) -> bool:
        return self.method != "aligned"


# --- tokens -------------------------------------------------------------------

def tokens(text: str, language: str) -> list[str]:
    """Lower-cased letter/number tokens; per character for unspaced languages."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"\{[^}]*\}|<[^>]*>|\[[^\]]*\]|\([^)]*\)", " ", text)   # tags, sound cues
    kept = "".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in text)
    if language in UNSPACED:
        return [c for c in kept if not c.isspace()]
    return kept.split()


def _run(language: str) -> int:
    return MIN_RUN_CHARS if language in UNSPACED else MIN_RUN_WORDS


# --- evidence -----------------------------------------------------------------

def find_anchors(cues: list, words: Iterable[TimedWord], language: str) -> list[Anchor]:
    """One anchor per cue that shares a run of identical tokens with the audio.

    The offset of a cue is the median over its matched tokens of (transcript time
    - subtitle time). The subtitle only has cue times, so a token's time is its
    position inside its cue; the median of many tokens averages that placement
    error out, and the fit across many cues averages it again.
    """
    sub_tokens: list[str] = []
    sub_cue: list[int] = []
    sub_time: list[float] = []
    for index, cue in enumerate(cues):
        pieces = tokens(cue.text if hasattr(cue, "text") else " ".join(cue.lines), language)
        for k, token in enumerate(pieces):
            sub_tokens.append(token)
            sub_cue.append(index)
            sub_time.append(cue.start + (cue.end - cue.start) * (k + 0.5) / len(pieces))
    asr_tokens: list[str] = []
    asr_time: list[float] = []
    for word in words:
        for token in tokens(word.text, language):
            asr_tokens.append(token)
            asr_time.append((word.start + word.end) / 2)

    matcher = difflib.SequenceMatcher(None, sub_tokens, asr_tokens, autojunk=False)
    per_cue: dict[int, list[float]] = {}
    for block in matcher.get_matching_blocks():
        if block.size < _run(language):
            continue
        for k in range(block.size):
            per_cue.setdefault(sub_cue[block.a + k], []).append(asr_time[block.b + k] - sub_time[block.a + k])
    anchors = []
    for index, offsets in per_cue.items():
        if len(offsets) < MIN_CUE_TOKENS:
            continue
        cue = cues[index]
        anchors.append(Anchor((cue.start + cue.end) / 2, statistics.median(offsets), index))
    anchors.sort(key=lambda a: a.time)
    return anchors


def drop_outliers(anchors: list[Anchor]) -> list[Anchor]:
    """Remove anchors that disagree with the anchors on BOTH sides of them.

    Checking both sides keeps the points next to a genuine step: they agree with
    the stretch they belong to even though they differ from the other one."""
    kept = []
    for i, anchor in enumerate(anchors):
        before = [a.offset for a in anchors[max(0, i - NEIGHBOURS):i]]
        after = [a.offset for a in anchors[i + 1:i + 1 + NEIGHBOURS]]
        agrees = [abs(anchor.offset - statistics.median(side)) <= OUTLIER_SECONDS for side in (before, after) if side]
        if not agrees or any(agrees):
            kept.append(anchor)
    return kept


# --- fitting ------------------------------------------------------------------

def _line(points: list[Anchor]) -> tuple[float, float, float]:
    """(offset at first point, slope, 90th-percentile residual) by a robust line.

    A first slope comes from the median of offset changes over half the span
    (long lags keep per-anchor noise from becoming slope error), then ordinary
    least squares on the anchors within INLIER_BAND of that line gives the
    final one, so a few bad anchors cannot move it."""
    t = [p.time for p in points]
    d = [p.offset for p in points]
    lag = max(1, len(points) // 2)
    slopes = [(d[i + lag] - d[i]) / (t[i + lag] - t[i]) for i in range(len(points) - lag) if t[i + lag] > t[i]]
    slope = max(-MAX_ABS_SLOPE, min(MAX_ABS_SLOPE, statistics.median(slopes) if slopes else 0.0))
    intercept = statistics.median(di - slope * (ti - t[0]) for ti, di in zip(t, d))
    for _ in range(2):
        inliers = [(ti, di) for ti, di in zip(t, d) if abs(di - (intercept + slope * (ti - t[0]))) <= INLIER_BAND]
        if len(inliers) < MIN_SEGMENT_POINTS:
            break
        mt = statistics.fmean(x for x, _ in inliers)
        md = statistics.fmean(y for _, y in inliers)
        var = sum((x - mt) ** 2 for x, _ in inliers)
        if var <= 0:
            break
        slope = max(-MAX_ABS_SLOPE, min(MAX_ABS_SLOPE, sum((x - mt) * (y - md) for x, y in inliers) / var))
        intercept = md - slope * (mt - t[0])
    residuals = sorted(abs(di - (intercept + slope * (ti - t[0]))) for ti, di in zip(t, d))
    return intercept, slope, residuals[int(0.9 * (len(residuals) - 1))]


def _largest_step(points: list[Anchor]) -> tuple[int, float]:
    window = max(MIN_SEGMENT_POINTS // 2, len(points) // 20)
    best, best_at = 0.0, 0
    for i in range(MIN_SEGMENT_POINTS, len(points) - MIN_SEGMENT_POINTS + 1):
        left = statistics.median(p.offset for p in points[max(0, i - window):i])
        right = statistics.median(p.offset for p in points[i:i + window])
        if abs(right - left) > best:
            best, best_at = abs(right - left), i
    return best_at, best


def _pieces(points: list[Anchor]) -> list[Piece]:
    intercept, slope, spread = _line(points)
    if spread > FIT_TOLERANCE and len(points) >= 2 * MIN_SEGMENT_POINTS:
        at, step = _largest_step(points)
        if step >= MIN_STEP:
            return _pieces(points[:at]) + _pieces(points[at:])
    t0, t1 = points[0].time, points[-1].time
    if abs(slope) * (t1 - t0) < MIN_DRIFT:
        slope = 0.0
        intercept = statistics.median(p.offset for p in points)
    return [Piece(t0, t1, intercept, slope)]


def fit(anchors: list[Anchor]) -> list[Piece]:
    return _pieces(anchors)


def piece_for(pieces: list[Piece], t: float) -> Piece:
    """The piece whose evidence span contains t, else the nearest one; the
    boundary between two pieces is the midpoint of the gap between their spans."""
    for i, piece in enumerate(pieces):
        upper = (piece.t1 + pieces[i + 1].t0) / 2 if i + 1 < len(pieces) else float("inf")
        if t < upper:
            return piece
    return pieces[-1]


# --- applying -----------------------------------------------------------------

def retime(cues: list, words: Iterable[TimedWord], language: str) -> tuple[list[tuple[float, float]], RetimeReport]:
    """New (start, end) per cue, in cue order, plus the evidence behind them.

    Raises RetimeRefused when the subtitle and the audio share too little text
    to trust a correction (wrong language, wrong episode, not the same film)."""
    anchors = find_anchors(cues, words, language)
    report = RetimeReport(cues=len(cues), anchored_cues=len(anchors))
    needed = max(MIN_ANCHORED_CUES, int(MIN_ANCHORED_FRACTION * len(cues)))
    if len(anchors) < needed:
        raise RetimeRefused(f"only {len(anchors)} of {len(cues)} cues share text with the audio "
                            f"(need {needed}); is this the same video, in this language?")
    kept = drop_outliers(anchors)
    if len(kept) < (1 - MAX_OUTLIER_FRACTION) * len(anchors):
        raise RetimeRefused(f"{len(anchors) - len(kept)} of {len(anchors)} matched cues disagree with "
                            "their neighbours; the subtitle does not follow the audio in order")
    anchors = kept
    pieces = fit(anchors)
    errors = sorted(abs(a.offset - piece_for(pieces, a.time).offset_at(a.time)) for a in anchors)
    report.residual_p50 = errors[len(errors) // 2]
    report.residual_p95 = errors[int(0.95 * (len(errors) - 1))]
    if report.residual_p95 > MAX_RESIDUAL_P95:
        raise RetimeRefused(f"the audio and subtitle disagree by up to {report.residual_p95:.1f} s "
                            "even after fitting steps and drift; not retiming")
    report.pieces = pieces
    report.max_offset = max(abs(p.offset_at(t)) for p in pieces for t in (p.t0, p.t1))
    if report.max_offset < ALIGNED_WITHIN:
        report.method = "aligned"
        return [(c.start, c.end) for c in cues], report
    report.method = ("constant offset" if len(pieces) == 1 and pieces[0].slope == 0 else
                     "linear drift" if len(pieces) == 1 else "stepped")

    times = []
    for cue in cues:
        piece = piece_for(pieces, (cue.start + cue.end) / 2)
        times.append((max(0.0, cue.start + piece.offset_at(cue.start)), max(0.0, cue.end + piece.offset_at(cue.end))))
    # A step can push a cue over its neighbour; trim the earlier one, never the later.
    for i in range(len(times) - 1):
        if times[i][1] > times[i + 1][0] and cues[i].end <= cues[i + 1].start:
            times[i] = (times[i][0], max(times[i][0] + 0.001, times[i + 1][0] - 0.001))
    return times, report
