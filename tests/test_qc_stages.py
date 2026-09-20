import tempfile
import unittest
from pathlib import Path

from glossary import Entity, build_glossary, protect
from hallucination import HallucinationFinding
from qc import entity_qc, output_qc, readability_qc, timing_qc, transcription_qc, translation_qc
from qc.types import JobQc, QcCategory, QcFinding, QcResult, QcStage
from segmentation_target import TargetCue
from srt import render
from transcript import Segment, Word


def seg(index, avg_logprob=-0.2, no_speech_prob=0.05, compression_ratio=1.3):
    return Segment(index=index, start=0.0, end=1.0, words=[], avg_logprob=avg_logprob,
                  no_speech_prob=no_speech_prob, compression_ratio=compression_ratio)


class TranscriptionQcTests(unittest.TestCase):
    def test_clean_segments_are_not_flagged(self):
        segments = [seg(0), seg(1)]
        findings = [HallucinationFinding(0, 0.0, [], False), HallucinationFinding(1, 0.0, [], False)]
        result = transcription_qc.run(segments, findings)
        self.assertEqual(result.population, 2)
        self.assertEqual(result.flagged, 0)

    def test_suppressed_segment_flagged_as_hallucination_category(self):
        segments = [seg(0)]
        findings = [HallucinationFinding(0, 0.9, ["signature:x"], True)]
        result = transcription_qc.run(segments, findings)
        self.assertEqual(result.flagged, 1)
        self.assertEqual(result.findings[0].category, QcCategory.HALLUCINATION)

    def test_low_confidence_segment_flagged(self):
        segments = [seg(0, avg_logprob=-1.2)]
        findings = [HallucinationFinding(0, 0.0, [], False)]
        result = transcription_qc.run(segments, findings)
        self.assertEqual(result.flagged, 1)


class TranslationQcTests(unittest.TestCase):
    def test_clean_translation_not_flagged(self):
        result = translation_qc.run(["Merhaba dunya"], ["Hello world"])
        self.assertEqual(result.population, 1)
        self.assertEqual(result.flagged, 0)

    def test_empty_translation_flagged(self):
        result = translation_qc.run(["Merhaba dunya nasilsin"], [""])
        self.assertEqual(result.flagged, 1)
        self.assertEqual(result.findings[0].category, QcCategory.TRANSLATION_ERROR)

    def test_leaked_placeholder_flagged(self):
        result = translation_qc.run(["source"], ["Xaa arrived"])
        self.assertEqual(result.findings[0].category, QcCategory.ENTITY_ERROR)

    def test_finding_evidence_carries_source_and_translated_text(self):
        # Real gap this closes (2026-09-19): a "substitution"/
        # "translation_error" finding used to carry only an index into a
        # coordinate space never persisted after the job finishes,
        # making it impossible to tell after the fact whether a flagged
        # instance was a genuine defect or a coincidental short-phrase
        # collision -- see qc/translation_qc.py's module docstring.
        result = translation_qc.run(["Merhaba dunya nasilsin"], [""])
        self.assertEqual(result.findings[0].evidence["source"], "Merhaba dunya nasilsin")
        self.assertEqual(result.findings[0].evidence["translated"], "")

    def test_substitution_finding_evidence_includes_the_other_matched_sentence(self):
        result = translation_qc.run(
            ["Tamam gidiyorum simdi", "Baska bir seyler burada"],
            ["okay going now", "okay going now"])
        finding = next(f for f in result.findings if f.category == QcCategory.SUBSTITUTION)
        self.assertEqual(finding.evidence["matches_source_of"], "Tamam gidiyorum simdi")
        self.assertEqual(finding.evidence["source"], "Baska bir seyler burada")
        self.assertEqual(finding.evidence["translated"], "okay going now")

    def test_unpunctuated_run_on_source_flagged(self):
        # Real bug (Love Is In The Air S01E03, 2026-09-21): this exact
        # source cue has zero sentence-ending punctuation across three
        # separate thoughts, so it went to NLLB as one generate() call
        # and came back as "Okay, Uncle Alptekin, I'm Moon Flood." --
        # invisible to the length-ratio checks above (37/84 = 0.44,
        # above the 0.25 cutoff for "suspiciously short").
        source = ("Sen Kahveni İçerken Ben Hazırladım Tamam Alptekin Amca "
                  "Ay Selinciğim Biz Amca Değil")
        translated = "Okay, Uncle Alptekin, I'm Moon Flood."
        result = translation_qc.run([source], [translated])
        self.assertEqual(result.flagged, 1)
        finding = result.findings[0]
        self.assertEqual(finding.category, QcCategory.TRANSLATION_ERROR)
        self.assertIn("unpunctuated", finding.reason)

    def test_ordinary_punctuated_sentence_not_flagged_as_run_on(self):
        result = translation_qc.run(
            ["Bu adam gercekten cok tuhaf davraniyor bu aralar sanki."],
            ["This guy has been acting really weird lately."])
        self.assertEqual(result.flagged, 0)

    def test_short_unpunctuated_source_not_flagged_as_run_on(self):
        # Under the 8-word threshold -- an ordinary short unpunctuated
        # utterance ("Tamam gidiyorum simdi") is common and not at risk
        # of the multi-clause garbling this check targets.
        result = translation_qc.run(["Tamam gidiyorum simdi"], ["Okay, I'm going now"])
        self.assertEqual(result.flagged, 0)


class EntityQcTests(unittest.TestCase):
    def test_matching_occurrence_counts_clean(self):
        g = build_glossary([Entity("Eda", ["Eda"])])
        source = protect("Eda geldi.", g)
        result = entity_qc.run(source, "Eda arrived.", g)
        self.assertEqual(result.flagged, 0)

    def test_dropped_entity_flagged(self):
        g = build_glossary([Entity("Eda", ["Eda"])])
        source = protect("Eda geldi.", g)
        result = entity_qc.run(source, "Someone arrived.", g)
        self.assertEqual(result.flagged, 1)
        self.assertEqual(result.findings[0].category, QcCategory.ENTITY_ERROR)

    def test_over_generation_is_not_flagged(self):
        # Real case (Love Is In The Air S01E01/E02, 2026-09-19): NLLB
        # expanding a pronoun to the character's name legitimately
        # produces MORE mentions in translation than source -- the
        # opposite of a dropped entity, and not something
        # entity_recovery() has any mechanism to (or should) undo.
        g = build_glossary([Entity("Eda", ["Eda"])])
        source = protect("Eda geldi.", g)
        result = entity_qc.run(source, "Eda arrived. Eda smiled.", g)
        self.assertEqual(result.flagged, 0)


class ReadabilityQcTests(unittest.TestCase):
    def test_normal_cue_clean(self):
        cue = TargetCue(start=0.0, end=2.0, lines=["Hello there."])
        self.assertEqual(readability_qc.run([cue]).flagged, 0)

    def test_too_many_lines_flagged(self):
        cue = TargetCue(start=0.0, end=2.0, lines=["one", "two", "three"])
        self.assertEqual(readability_qc.run([cue]).flagged, 1)

    def test_too_short_duration_flagged(self):
        cue = TargetCue(start=0.0, end=0.2, lines=["Hi"])
        self.assertEqual(readability_qc.run([cue]).flagged, 1)


class TimingQcTests(unittest.TestCase):
    def test_monotonic_clean(self):
        cues = [TargetCue(0.0, 1.0, ["a"]), TargetCue(1.0, 2.0, ["b"])]
        self.assertEqual(timing_qc.run(cues).flagged, 0)

    def test_overlap_flagged(self):
        cues = [TargetCue(0.0, 2.0, ["a"]), TargetCue(1.0, 3.0, ["b"])]
        self.assertEqual(timing_qc.run(cues).flagged, 1)


class OutputQcTests(unittest.TestCase):
    def test_valid_srt_clean(self):
        cues = [TargetCue(0.0, 1.0, ["Hello"])]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.srt"
            path.write_text(render(cues), encoding="utf-8")
            result = output_qc.run(path)
        self.assertEqual(result.flagged, 0)
        self.assertEqual(result.population, 1)


class NeedsReviewCountTests(unittest.TestCase):
    """Production-readiness gap (2026-09-21): translation/entity/
    hallucination/readability QC findings are purely advisory -- they
    never gate job completion, matching a real, session-proven false-
    positive rate too high to safely auto-fail on. needs_review_count()
    surfaces the subset actually worth a human's attention without
    changing completion behavior at all."""

    def test_entity_error_finding_counts_regardless_of_confidence(self):
        qc = JobQc(entity=QcResult(
            stage=QcStage.ENTITY, population=1, flagged=1,
            findings=[QcFinding(QcCategory.ENTITY_ERROR, "dropped", confidence=0.3)]))
        self.assertEqual(qc.needs_review_count(), 1)

    def test_hallucination_finding_counts_regardless_of_confidence(self):
        qc = JobQc(transcription=QcResult(
            stage=QcStage.TRANSCRIPTION, population=1, flagged=1,
            findings=[QcFinding(QcCategory.HALLUCINATION, "suppressed", confidence=0.2)]))
        self.assertEqual(qc.needs_review_count(), 1)

    def test_high_confidence_finding_counts_regardless_of_category(self):
        qc = JobQc(translation=QcResult(
            stage=QcStage.TRANSLATION, population=1, flagged=1,
            findings=[QcFinding(QcCategory.TRANSLATION_ERROR, "empty translation", confidence=1.0)]))
        self.assertEqual(qc.needs_review_count(), 1)

    def test_low_confidence_substitution_does_not_count(self):
        # Real false-positive shape this session found repeatedly:
        # harmless interjection-collision "substitution" matches at
        # moderate confidence -- must not trigger review noise.
        qc = JobQc(translation=QcResult(
            stage=QcStage.TRANSLATION, population=2, flagged=1,
            findings=[QcFinding(QcCategory.SUBSTITUTION, "repeated translation", confidence=0.5)]))
        self.assertEqual(qc.needs_review_count(), 0)

    def test_structural_findings_alone_never_count(self):
        qc = JobQc(timing=QcResult(stage=QcStage.TIMING, population=5, flagged=1,
                                   findings=[QcFinding(QcCategory.TIMING_DIFFERENCE, "gap", confidence=0.6)]),
                  readability=QcResult(stage=QcStage.READABILITY, population=5, flagged=1,
                                       findings=[QcFinding(QcCategory.READABILITY_ERROR, "cps", confidence=0.5)]))
        self.assertEqual(qc.needs_review_count(), 0)

    def test_no_qc_stages_present_counts_zero(self):
        self.assertEqual(JobQc().needs_review_count(), 0)

    def test_counts_across_multiple_stages(self):
        qc = JobQc(
            entity=QcResult(stage=QcStage.ENTITY, population=1, flagged=1,
                           findings=[QcFinding(QcCategory.ENTITY_ERROR, "dropped", confidence=1.0)]),
            translation=QcResult(stage=QcStage.TRANSLATION, population=1, flagged=1,
                                findings=[QcFinding(QcCategory.TRANSLATION_ERROR, "empty", confidence=1.0)]))
        self.assertEqual(qc.needs_review_count(), 2)


if __name__ == "__main__":
    unittest.main()
