"""Source grouping and source->target timestamp projection, with
provenance and N:M-aware coverage validation.

Coverage is tracked per *group* (a merged source-cue run), never per
individual source cue, so an internal split point inside a long
translated group can never be mistaken for a missing source cue -- the
original design rationale, still correct. Group IDENTITY, however, is
read directly from each cue's `source_group_index` provenance rather
than inferred from timing coincidence -- see validate_coverage()'s
docstring for the real full-episode-scale bug that inference approach
had and why provenance-based tracking replaced it.
"""

from __future__ import annotations

from dataclasses import dataclass

from qc.types import QcCategory, QcFinding, QcResult, QcStage
from transcript import MERGEABLE_BOUNDARIES, Segment

MAX_MERGE_DURATION = 7.0
TOLERANCE = 0.01   # SRT millisecond rounding is the only source of imprecision


@dataclass
class SourceGroup:
    indices: list[int]        # positions into the source cue list
    start: float
    end: float


def merge_groups(source_cues: list[Segment]) -> list[SourceGroup]:
    """Partition source cues into translation units: consecutive cues
    merge only when the boundary between them is provably display-only
    (MERGEABLE_BOUNDARIES) and the merged envelope still fits
    MAX_MERGE_DURATION. Absence of boundary provenance (no
    boundary_before recorded) degrades to one cue per group -- the safe,
    historical 1:1 default."""
    if not source_cues:
        return []
    groups: list[SourceGroup] = []
    cur = [0]
    for i in range(1, len(source_cues)):
        reason = source_cues[i].boundary_before
        envelope = source_cues[i].end - source_cues[cur[0]].start
        if reason in MERGEABLE_BOUNDARIES and envelope <= MAX_MERGE_DURATION:
            cur.append(i)
        else:
            groups.append(SourceGroup(cur, source_cues[cur[0]].start, source_cues[cur[-1]].end))
            cur = [i]
    groups.append(SourceGroup(cur, source_cues[cur[0]].start, source_cues[cur[-1]].end))
    return groups


@dataclass
class ProjectedCue:
    start: float
    end: float
    lines: list[str]
    source_group_index: int       # which SourceGroup this cue's content was translated from
    source_indices: list[int]     # the specific source cue indices covered

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def project(groups: list[SourceGroup], target_cues_by_group: list[list]) -> list[ProjectedCue]:
    """target_cues_by_group[i] is the list of (already timed) target cues
    produced for groups[i] -- e.g. segmentation_target.segment()'s output
    for that group's translated text. Flattens them into ProjectedCue with
    explicit provenance back to the source group and cue indices.

    Preserves `.lines` (not just a flattened `.text`) -- an earlier
    version of this function collapsed segmentation_target's readability-
    driven line wrapping into one line here, which both the rendered SRT
    and readability_qc then silently lost. Confirmed by a real-audio
    integration run: 33/56 output cues wrongly flagged "line exceeds max
    length" because the wrap had already been computed correctly and then
    discarded at this exact step."""
    out = []
    for gi, (group, cues) in enumerate(zip(groups, target_cues_by_group)):
        for cue in cues:
            lines = list(getattr(cue, "lines", None) or [getattr(cue, "text", str(cue))])
            out.append(ProjectedCue(start=cue.start, end=cue.end, lines=lines,
                                    source_group_index=gi, source_indices=list(group.indices)))
    return out


def validate_coverage(source_cues: list[Segment], groups: list[SourceGroup],
                      projected: list[ProjectedCue]) -> QcResult:
    """Confirms every source group has translated cues, in order, with no
    invented or shifted time window.

    Group identity is read from each cue's explicit `source_group_index`
    provenance, never inferred from timing coincidence. A real bug this
    fixes, found only by a full-episode (2h08m) production run -- never
    reproduced at any shorter real-audio sample, because it needs a
    zero-duration source cue to trigger, and a zero-width ASR word
    timestamp is rare enough not to show up in a 15-minute sample: the
    prior implementation inferred which group a cue belonged to by
    walking `cue.end >= groups[k].end - TOLERANCE`. When a zero-duration
    group's `.end` exactly coincided with the *previous* group's `.end`
    (both landing on the same source timestamp), that single check was
    satisfied twice by one cue, silently absorbing the zero-duration
    group's coverage into the previous group's cue -- without ever
    consuming the zero-duration group's own dedicated cue from the list.
    The walk then desynced by one group for the remainder of the episode,
    surfacing as a false "dropped utterance" on a later, entirely
    unrelated group. Reading `source_group_index` directly sidesteps the
    whole class of timing-coincidence bug: it is exact, structural
    provenance already carried by ProjectedCue for exactly this purpose.

    Per-group timing is still verified separately, against each group's
    real envelope -- so a genuine timing bug (an invented or shifted
    window) is still caught; only GROUP IDENTITY is no longer inferred."""
    G = len(groups)
    findings: list[QcFinding] = []

    cues_by_group: dict[int, list[ProjectedCue]] = {}
    prev_gi = -1
    for t_index, cue in enumerate(projected, 1):
        gi = cue.source_group_index
        if gi < prev_gi:
            findings.append(QcFinding(
                category=QcCategory.TIMING_DIFFERENCE,
                reason=f"translated cue {t_index} maps back to an earlier source group",
                confidence=1.0, index=groups[gi].indices[0]))
            return QcResult(stage=QcStage.SEGMENTATION, population=G, flagged=1, findings=findings)
        prev_gi = gi
        cues_by_group.setdefault(gi, []).append(cue)

    for gi, group in enumerate(groups):
        cues = cues_by_group.get(gi)
        if not cues:
            anchor = group.indices[0]
            findings.append(QcFinding(
                category=QcCategory.DROPPED_UTTERANCE,
                reason=f"source cue {anchor + 1} has no corresponding translated cue",
                confidence=1.0, index=anchor))
            return QcResult(stage=QcStage.SEGMENTATION, population=G, flagged=1, findings=findings)
        if abs(cues[0].start - group.start) > TOLERANCE or abs(cues[-1].end - group.end) > TOLERANCE:
            findings.append(QcFinding(
                category=QcCategory.TIMING_DIFFERENCE,
                reason=(f"group {gi}'s translated cues span [{cues[0].start:.3f}-{cues[-1].end:.3f}]s, "
                       f"not the source group's real envelope [{group.start:.3f}-{group.end:.3f}]s"),
                confidence=1.0, index=group.indices[0]))
            return QcResult(stage=QcStage.SEGMENTATION, population=G, flagged=1, findings=findings)

    return QcResult(stage=QcStage.SEGMENTATION, population=G, flagged=0, findings=[])
