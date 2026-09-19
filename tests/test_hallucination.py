"""hallucination.py -- the mandatory new detection stage.

Includes the confirmed real-world regression: faster-whisper large-v3
inserting "Altyazı M.K." mid-sentence into real dialogue, found during the
Season 01 audit (2026-09-11, 26 occurrences across 5 episodes). The
signature-registry entry for it lives in hallucination_signatures.json,
not in this test or in hallucination.py -- this test proves the *pipeline*
catches it via that registry, not that the string is special-cased in code.
"""

import unittest

from hallucination import detect, load_signatures, score_segment
from transcript import Segment, Word


def make_segment(index, start, end, text, *, avg_logprob=-0.3, no_speech_prob=0.05,
                 compression_ratio=1.5):
    words = [Word(text=w, original_text=w, start=start, end=end, probability=0.9)
            for w in text.split()]
    return Segment(index=index, start=start, end=end, words=words, avg_logprob=avg_logprob,
                  no_speech_prob=no_speech_prob, compression_ratio=compression_ratio)


class KnownSignatureTests(unittest.TestCase):
    """The confirmed real regression."""

    def test_altyazi_mk_is_caught_by_registry(self):
        segments = [
            make_segment(0, 100.0, 102.0, "Bütün Türkiye adamın peşinde normal dialogue here"),
            make_segment(1, 7521.976, 7524.505, "Bütün Altyazı M.K. Türkiye adamın peşinde."),
        ]
        findings = detect(segments, language="tr")
        hit = findings[1]
        self.assertGreaterEqual(hit.score, 0.9)
        self.assertTrue(any("signature:tr-subtitle-credit-altyazi" in r for r in hit.reasons))
        self.assertTrue(hit.suppress)

    def test_legitimate_dialogue_without_the_pattern_is_untouched(self):
        segments = [make_segment(0, 0.0, 2.0, "Eda Eda kızım hadi uyan")]
        findings = detect(segments, language="tr")
        self.assertEqual(findings[0].score, 0.0)
        self.assertFalse(findings[0].suppress)
        self.assertEqual(findings[0].reasons, [])

    def test_thanks_for_watching_is_caught_by_registry(self):
        # Real measured case (Love Is In The Air S01E01, 2026-09-17 audit):
        # acoustic evidence alone misses this -- low no_speech_prob and
        # compression_ratio, avg_logprob above the low-confidence threshold,
        # and only 2 occurrences in the episode (below the recurrence gate).
        segments = [
            make_segment(0, 60.0, 70.0, "Eda kızım hadi kalk dükkana gidiyoruz",
                        avg_logprob=-0.2, no_speech_prob=0.02, compression_ratio=1.5),
            make_segment(1, 71.41, 74.21, "İzlediğiniz için teşekkürler.",
                        avg_logprob=-0.396, no_speech_prob=0.0045, compression_ratio=0.79),
        ]
        findings = detect(segments, language="tr")
        hit = findings[1]
        self.assertGreaterEqual(hit.score, 0.9)
        self.assertTrue(any("signature:tr-thanks-for-watching" in r for r in hit.reasons))
        self.assertTrue(hit.suppress)
        self.assertEqual(findings[0].score, 0.0)   # unrelated dialogue untouched

    def test_signature_is_language_scoped(self):
        # An English segment containing the Turkish word "altyazı" would be
        # bizarre -- but the signature must not fire outside its declared
        # language regardless.
        segments = [make_segment(0, 0.0, 1.0, "altyazı appears here")]
        findings = detect(segments, language="en")
        self.assertEqual(findings[0].score, 0.0)


class AcousticEvidenceTests(unittest.TestCase):
    """Evidence-based detection must work on artifacts with NO registered
    signature -- this is what makes the detector generalize."""

    def test_high_compression_ratio_flags_degenerate_text(self):
        seg = make_segment(0, 0.0, 2.0, "tekrar tekrar tekrar tekrar tekrar",
                           compression_ratio=3.0)
        finding = score_segment(seg, [seg], 100.0, load_signatures(), "tr")
        self.assertGreater(finding.score, 0.0)
        self.assertTrue(any("compression_ratio" in r for r in finding.reasons))

    def test_no_speech_prob_contradiction_flags_text_during_likely_silence(self):
        seg = make_segment(0, 0.0, 2.0, "unexpected words here", no_speech_prob=0.9)
        finding = score_segment(seg, [seg], 100.0, load_signatures(), "tr")
        self.assertGreater(finding.score, 0.0)
        self.assertTrue(any("no_speech_prob" in r for r in finding.reasons))

    def test_low_confidence_alone_is_flagged_but_not_suppressed(self):
        seg = make_segment(0, 0.0, 2.0, "muffled unclear speech", avg_logprob=-1.5)
        finding = score_segment(seg, [seg], 100.0, load_signatures(), "tr")
        self.assertGreater(finding.score, 0.0)
        self.assertFalse(finding.suppress)   # one weak signal alone must not suppress

    def test_clean_high_confidence_segment_scores_zero(self):
        seg = make_segment(0, 0.0, 2.0, "Merhaba nasılsın", avg_logprob=-0.1,
                           no_speech_prob=0.02, compression_ratio=1.2)
        finding = score_segment(seg, [seg], 100.0, load_signatures(), "tr")
        self.assertEqual(finding.score, 0.0)


class RecurrenceTests(unittest.TestCase):
    """Catches unknown artifacts by their recurrence shape, not their
    literal text -- must generalize beyond the one known signature."""

    def test_widely_spread_verbatim_phrase_is_suspicious(self):
        episode_duration = 1000.0
        text = "bu dizinin altyazısı bilinmeyen bir kaynak tarafından"
        segments = [make_segment(i, t, t + 2, text) for i, t in
                   enumerate([10.0, 300.0, 600.0, 950.0])]
        findings = detect(segments, language="tr")
        self.assertTrue(any("recurrence_score" in r for r in findings[0].reasons))

    def test_common_single_word_acknowledgement_is_never_flagged_by_recurrence(self):
        # Real measured case: "Evet." recurs 15 times in one real episode.
        segments = [make_segment(i, t, t + 0.5, "Evet.") for i, t in
                   enumerate([i * 400.0 for i in range(15)])]
        findings = detect(segments, language="tr")
        for f in findings:
            self.assertFalse(any("recurrence_score" in r for r in f.reasons), f.reasons)
            self.assertFalse(f.suppress)

    def test_common_two_word_phrase_is_never_flagged_by_recurrence(self):
        # Real measured case (Love Is In The Air S01E01/E02, 2026-09-19):
        # "Teşekkür Ederim" ("thank you"), "Öyle Mi" ("is that so?"), and
        # "İyi Misin" ("are you okay?") each recurred 3x in a real episode
        # with hallucination_score=0.00 on every other signal, yet were
        # flagged by recurrence alone under the old `>= 2 words` gate.
        for phrase in ("Teşekkür Ederim", "Öyle Mi", "İyi Misin"):
            segments = [make_segment(i, t, t + 1.0, phrase) for i, t in
                       enumerate([100.0, 1500.0, 2800.0])]
            findings = detect(segments, language="tr")
            for f in findings:
                self.assertFalse(any("recurrence_score" in r for r in f.reasons),
                                 (phrase, f.reasons))
                self.assertFalse(f.suppress)

    def test_a_name_mentioned_several_times_is_not_suppressed(self):
        # A character's name recurring across a scene is completely normal
        # -- must never reach the suppression threshold on recurrence alone.
        segments = [make_segment(i, t, t + 1.0, "Serkan Bolat.") for i, t in
                   enumerate([50.0, 500.0, 3000.0])]
        findings = detect(segments, language="tr")
        for f in findings:
            self.assertFalse(f.suppress)

    def test_locally_repeated_dialogue_in_one_scene_is_not_suppressed(self):
        # Two consecutive utterances of the same short line, close in time
        # -- ordinary emphatic repetition ("Hadi, hadi!"), not a fixation.
        segments = [make_segment(0, 10.0, 11.0, "Hadi kalk"),
                   make_segment(1, 11.2, 12.2, "Hadi kalk")]
        findings = detect(segments, language="tr")
        for f in findings:
            self.assertFalse(f.suppress)


class DetectMutatesTranscriptTests(unittest.TestCase):
    def test_detect_writes_findings_onto_the_segment_objects(self):
        segments = [make_segment(0, 7521.976, 7524.505, "Bütün Altyazı M.K. Türkiye adamın peşinde.")]
        detect(segments, language="tr")
        self.assertGreater(segments[0].hallucination_score, 0.0)
        self.assertTrue(segments[0].suppressed)
        self.assertTrue(segments[0].is_suspect)


if __name__ == "__main__":
    unittest.main()
