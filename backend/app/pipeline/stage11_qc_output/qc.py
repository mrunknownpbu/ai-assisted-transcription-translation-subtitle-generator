"""Stage 11 (part 1): Quality Control.

Every check below is recorded for every cue — pass or fail — because the spec requires
every QC decision to be tagged with pass/fail reasoning, not just the failures. A report
"passes" overall only if every individual finding passed.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.pipeline.interfaces import QcFinding, QcReport, TargetCue

_OVERLAP_TOLERANCE_S = 1e-6


@dataclass(frozen=True)
class QcConfig:
    max_chars_per_line: int
    max_lines_per_cue: int
    max_reading_cps: float
    min_cue_duration_s: float


def _check_single_cue(cue: TargetCue, config: QcConfig) -> list[QcFinding]:
    findings: list[QcFinding] = []

    is_empty = not cue.text.strip()
    findings.append(QcFinding(check="non_empty_text", passed=not is_empty,
                               reason="cue text is empty" if is_empty else "cue has text", cue_id=cue.cue_id))
    if is_empty:
        return findings  # remaining checks are meaningless on empty text

    duration = cue.end - cue.start
    duration_ok = bool(duration >= config.min_cue_duration_s)
    findings.append(QcFinding(
        check="min_duration", passed=duration_ok,
        reason=f"duration={duration:.3f}s vs minimum {config.min_cue_duration_s}s", cue_id=cue.cue_id,
    ))

    char_count = len(cue.text.replace("\n", " "))
    cps = char_count / duration if duration > 0 else float("inf")
    cps_ok = bool(cps <= config.max_reading_cps)
    findings.append(QcFinding(
        check="reading_speed", passed=cps_ok,
        reason=f"{cps:.1f} chars/sec vs maximum {config.max_reading_cps}", cue_id=cue.cue_id,
    ))

    lines = cue.text.split("\n")
    line_count_ok = len(lines) <= config.max_lines_per_cue
    findings.append(QcFinding(
        check="line_count", passed=line_count_ok,
        reason=f"{len(lines)} line(s) vs maximum {config.max_lines_per_cue}", cue_id=cue.cue_id,
    ))

    longest_line = max((len(l) for l in lines), default=0)
    line_length_ok = longest_line <= config.max_chars_per_line
    findings.append(QcFinding(
        check="line_length", passed=line_length_ok,
        reason=f"longest line {longest_line} chars vs maximum {config.max_chars_per_line}", cue_id=cue.cue_id,
    ))

    return findings


def run_qc(cues: list[TargetCue], config: QcConfig, target_language: str | None = None) -> QcReport:
    findings: list[QcFinding] = []

    for i, cue in enumerate(cues):
        findings.extend(_check_single_cue(cue, config))

        if i + 1 < len(cues):
            next_cue = cues[i + 1]
            overlap = cue.end - next_cue.start
            overlap_ok = bool(overlap <= _OVERLAP_TOLERANCE_S)
            findings.append(QcFinding(
                check="no_overlap", passed=overlap_ok,
                reason=(f"cue ends at {cue.end:.3f}s, next cue starts at {next_cue.start:.3f}s"
                        + (f" (overlaps by {overlap:.3f}s)" if not overlap_ok else " (no overlap)")),
                cue_id=cue.cue_id,
            ))

    passed = all(f.passed for f in findings)
    return QcReport(stage="stage11_qc", target_language=target_language, passed=passed, findings=findings)
