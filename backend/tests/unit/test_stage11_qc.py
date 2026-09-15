import pytest

from app.pipeline.interfaces import TargetCue
from app.pipeline.stage11_qc_output.qc import QcConfig, run_qc

CONFIG = QcConfig(max_chars_per_line=42, max_lines_per_cue=2, max_reading_cps=21.0, min_cue_duration_s=0.8)


def _cue(cue_id, start, end, text):
    return TargetCue(cue_id=cue_id, start=start, end=end, text=text, source_chunk_ids=["c1"], char_share_of_chunk=1.0)


@pytest.mark.unit
def test_healthy_cues_pass_every_check():
    cues = [_cue("c1", 0.0, 2.0, "Hello there"), _cue("c2", 2.5, 4.5, "How are you")]
    report = run_qc(cues, CONFIG)
    assert report.passed is True
    assert all(f.passed for f in report.findings)


@pytest.mark.unit
def test_empty_cue_fails_and_skips_further_checks():
    report = run_qc([_cue("c1", 0.0, 2.0, "   ")], CONFIG)
    assert report.passed is False
    checks = [f.check for f in report.findings]
    assert checks == ["non_empty_text"]  # no duration/cps/etc. checks for an empty cue


@pytest.mark.unit
def test_too_short_duration_fails_min_duration_check():
    report = run_qc([_cue("c1", 0.0, 0.2, "Hi")], CONFIG)
    finding = next(f for f in report.findings if f.check == "min_duration")
    assert finding.passed is False
    assert report.passed is False


@pytest.mark.unit
def test_excessive_reading_speed_fails_cps_check():
    long_text = "word " * 40  # way too many characters for a 1-second cue
    report = run_qc([_cue("c1", 0.0, 1.0, long_text.strip())], CONFIG)
    finding = next(f for f in report.findings if f.check == "reading_speed")
    assert finding.passed is False


@pytest.mark.unit
def test_too_many_lines_fails_line_count_check():
    report = run_qc([_cue("c1", 0.0, 3.0, "line one\nline two\nline three")], CONFIG)
    finding = next(f for f in report.findings if f.check == "line_count")
    assert finding.passed is False


@pytest.mark.unit
def test_overlapping_cues_fail_overlap_check():
    cues = [_cue("c1", 0.0, 3.0, "hello"), _cue("c2", 2.0, 4.0, "world")]
    report = run_qc(cues, CONFIG)
    finding = next(f for f in report.findings if f.check == "no_overlap")
    assert finding.passed is False
    assert report.passed is False


@pytest.mark.unit
def test_non_overlapping_adjacent_cues_pass_overlap_check():
    cues = [_cue("c1", 0.0, 2.0, "hello"), _cue("c2", 2.0, 4.0, "world")]
    report = run_qc(cues, CONFIG)
    finding = next(f for f in report.findings if f.check == "no_overlap")
    assert finding.passed is True


@pytest.mark.unit
def test_every_finding_carries_a_reason_and_cue_id():
    report = run_qc([_cue("c1", 0.0, 2.0, "Hello")], CONFIG)
    for finding in report.findings:
        assert finding.reason
        assert finding.cue_id == "c1"
