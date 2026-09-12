import tempfile
import unittest
from pathlib import Path

from glossary import Entity, build_glossary, protect
from hallucination import HallucinationFinding
from qc import entity_qc, output_qc, readability_qc, timing_qc, transcription_qc, translation_qc
from qc.types import QcCategory
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


if __name__ == "__main__":
    unittest.main()
