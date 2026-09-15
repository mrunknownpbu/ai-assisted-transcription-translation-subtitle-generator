"""Final output-file QC — re-parses the SRT file this platform just wrote, independently
of the renderer that produced it, and validates it as a last gate before the job reports
success. Catches anything a rendering bug could introduce that the pre-render cue-level QC
(`qc.py`) wouldn't see, since that runs on the in-memory `TargetCue` objects, not the bytes
actually written to disk.
"""
from __future__ import annotations

from pathlib import Path

from app.pipeline.interfaces import QcFinding, QcReport
from app.pipeline.stage11_qc_output.formatters.srt import parse_srt

_OVERLAP_TOLERANCE_S = 1e-6


def run(srt_path: str | Path, target_language: str | None = None) -> QcReport:
    path = Path(srt_path)
    findings: list[QcFinding] = []

    try:
        cues = parse_srt(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return QcReport(stage="output_qc", target_language=target_language, passed=False,
                         findings=[QcFinding(check="output_file_parses", passed=False, reason=str(exc))])

    findings.append(QcFinding(check="output_file_parses", passed=True, reason=f"parsed {len(cues)} cue(s)"))

    for i, cue in enumerate(cues):
        is_empty = not cue.text.strip()
        findings.append(QcFinding(check="output_cue_non_empty", passed=not is_empty, cue_id=str(cue.index),
                                   reason="cue text is empty" if is_empty else "cue has text"))

        duration_ok = (cue.end - cue.start) > 0
        findings.append(QcFinding(check="output_cue_positive_duration", passed=duration_ok, cue_id=str(cue.index),
                                   reason=f"duration={cue.end - cue.start:.3f}s"))

        if i + 1 < len(cues):
            next_cue = cues[i + 1]
            overlap_ok = (cue.end - next_cue.start) <= _OVERLAP_TOLERANCE_S
            findings.append(QcFinding(
                check="output_cue_no_overlap", passed=overlap_ok, cue_id=str(cue.index),
                reason=f"ends at {cue.end:.3f}s, next cue starts at {next_cue.start:.3f}s",
            ))

    passed = all(f.passed for f in findings)
    return QcReport(stage="output_qc", target_language=target_language, passed=passed, findings=findings)
