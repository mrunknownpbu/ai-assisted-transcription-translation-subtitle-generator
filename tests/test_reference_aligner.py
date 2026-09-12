import unittest

from reference_aligner import AlignmentCategory, Cue, align, summarize


def cat(result):
    return result.category


class CorrectTests(unittest.TestCase):
    def test_identical_text_and_timing_is_correct(self):
        gen = [Cue(0.0, 2.0, "Hello there.")]
        ref = [Cue(0.1, 2.1, "Hello there.")]
        results = align(gen, ref)
        self.assertEqual(len(results), 1)
        self.assertEqual(cat(results[0]), AlignmentCategory.CORRECT)

    def test_sequence_of_correct_matches(self):
        gen = [Cue(0.0, 2.0, "One."), Cue(2.0, 4.0, "Two."), Cue(4.0, 6.0, "Three.")]
        ref = [Cue(0.0, 2.0, "One."), Cue(2.0, 4.0, "Two."), Cue(4.0, 6.0, "Three.")]
        results = align(gen, ref)
        self.assertEqual([cat(r) for r in results],
                         [AlignmentCategory.CORRECT] * 3)


class NormalizationDifferenceTests(unittest.TestCase):
    def test_near_exact_single_word_variant(self):
        # Word-token matching (see reference_aligner._lexical_similarity)
        # is order-sensitive but not char-fuzzy: "happy"/"glad" are simply
        # different tokens, not a "close spelling." NORMALIZATION_DIFFERENCE
        # is reachable at this granularity as "same sentence, one or two
        # words swapped for a near-synonym" -- high similarity, single
        # cue each side, good timing, but not an exact normalized match.
        gen = [Cue(10.0, 12.0, "I am really very happy today")]
        ref = [Cue(10.0, 12.0, "I am really very glad today")]
        results = align(gen, ref)
        self.assertEqual(len(results), 1)
        self.assertEqual(cat(results[0]), AlignmentCategory.NORMALIZATION_DIFFERENCE)


class SegmentationDifferenceTests(unittest.TestCase):
    def test_one_generated_cue_matches_two_reference_cues_same_combined_text(self):
        gen = [Cue(20.0, 24.0, "I am going home now to sleep")]
        ref = [Cue(20.0, 22.0, "I am going home"), Cue(22.0, 24.0, "now to sleep")]
        results = align(gen, ref)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(cat(r), AlignmentCategory.SEGMENTATION_DIFFERENCE)
        self.assertEqual(len(r.gen_cues), 1)
        self.assertEqual(len(r.ref_cues), 2)

    def test_two_generated_cues_match_one_reference_cue(self):
        gen = [Cue(30.0, 31.5, "The weather is nice"), Cue(31.5, 33.0, "today in the city")]
        ref = [Cue(30.0, 33.0, "The weather is nice today in the city")]
        results = align(gen, ref)
        self.assertEqual(len(results), 1)
        self.assertEqual(cat(results[0]), AlignmentCategory.SEGMENTATION_DIFFERENCE)


class TimingDifferenceTests(unittest.TestCase):
    def test_same_text_far_apart_in_time(self):
        gen = [Cue(100.0, 102.0, "Come here right now.")]
        ref = [Cue(130.0, 132.0, "Come here right now.")]
        results = align(gen, ref)
        self.assertEqual(len(results), 1)
        self.assertEqual(cat(results[0]), AlignmentCategory.TIMING_DIFFERENCE)


class DroppedUtteranceTests(unittest.TestCase):
    def test_reference_only_line_with_no_generated_counterpart(self):
        gen = [Cue(0.0, 2.0, "hello there friend"), Cue(4.0, 6.0, "goodbye for now")]
        ref = [Cue(0.0, 2.0, "hello there friend"),
              Cue(2.0, 4.0, "completely unrelated inserted aardvark content"),
              Cue(4.0, 6.0, "goodbye for now")]
        results = align(gen, ref)
        categories = [cat(r) for r in results]
        self.assertIn(AlignmentCategory.DROPPED_UTTERANCE, categories)
        dropped = [r for r in results if cat(r) == AlignmentCategory.DROPPED_UTTERANCE]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].ref_cues[0].text, "completely unrelated inserted aardvark content")
        # the two genuinely matching lines on either side must still be recognized
        self.assertEqual(categories.count(AlignmentCategory.CORRECT), 2)


class InsertedContentTests(unittest.TestCase):
    def test_generated_only_line_with_no_reference_counterpart(self):
        gen = [Cue(0.0, 2.0, "hello there friend"),
              Cue(2.0, 4.0, "purely hallucinated zebra content"),
              Cue(4.0, 6.0, "goodbye for now")]
        ref = [Cue(0.0, 2.0, "hello there friend"), Cue(4.0, 6.0, "goodbye for now")]
        results = align(gen, ref)
        categories = [cat(r) for r in results]
        self.assertIn(AlignmentCategory.INSERTED_CONTENT, categories)
        inserted = [r for r in results if cat(r) == AlignmentCategory.INSERTED_CONTENT]
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0].gen_cues[0].text, "purely hallucinated zebra content")
        self.assertEqual(categories.count(AlignmentCategory.CORRECT), 2)


class SubstitutionTests(unittest.TestCase):
    def test_same_time_slot_partially_related_but_wrong_content(self):
        # Some genuine word overlap (the scene/subject matches) but the
        # actual content substantively differs -- distinct from UNKNOWN,
        # which is for cues sharing no discernible relationship at all.
        gen = [Cue(50.0, 52.0, "The cat sat quietly on the mat")]
        ref = [Cue(50.0, 52.0, "The cat knocked the lamp off the table")]
        results = align(gen, ref)
        self.assertEqual(len(results), 1)
        self.assertEqual(cat(results[0]), AlignmentCategory.SUBSTITUTION)


class UnknownTests(unittest.TestCase):
    def test_same_time_slot_zero_shared_characters(self):
        gen = [Cue(60.0, 62.0, "12345")]
        ref = [Cue(60.0, 62.0, "abcde")]
        results = align(gen, ref)
        self.assertEqual(len(results), 1)
        self.assertEqual(cat(results[0]), AlignmentCategory.UNKNOWN)


class SemanticFnTests(unittest.TestCase):
    """semantic_fn is exercised here with a plain mock -- these tests
    cover the WIRING (max-of-both, never GPU-required, field population),
    not any real embedding model. See reference_aligner.nllb_semantic_similarity
    for the real (GPU-requiring) backend, which is deliberately never
    imported or called from this test file."""

    def test_semantic_fn_rescues_a_lexical_miss(self):
        # Zero shared word-tokens -- lexical-only would call this
        # SUBSTITUTION or UNKNOWN (see SubstitutionTests/UnknownTests
        # above). A semantic_fn asserting these are equivalent must be
        # able to raise the classification, since max() takes the
        # stronger of the two signals.
        gen = [Cue(10.0, 12.0, "get a medical degree")]
        ref = [Cue(10.0, 12.0, "finish studying medicine")]
        no_overlap = align(gen, ref)[0]
        self.assertIn(cat(no_overlap), (AlignmentCategory.SUBSTITUTION, AlignmentCategory.UNKNOWN))

        with_semantic = align(gen, ref, semantic_fn=lambda a, b: 0.9)[0]
        self.assertEqual(cat(with_semantic), AlignmentCategory.NORMALIZATION_DIFFERENCE)
        self.assertEqual(with_semantic.semantic_similarity, 0.9)

    def test_semantic_fn_does_not_rescue_a_genuine_mismatch(self):
        # A LOW semantic score must not manufacture a match that isn't
        # there -- max(low_lexical, low_semantic) stays low.
        gen = [Cue(10.0, 12.0, "The cat sat on the mat")]
        ref = [Cue(10.0, 12.0, "Purple elephants dance wildly")]
        result = align(gen, ref, semantic_fn=lambda a, b: 0.05)[0]
        self.assertNotIn(cat(result), (AlignmentCategory.CORRECT, AlignmentCategory.NORMALIZATION_DIFFERENCE))

    def test_semantic_fn_never_weakens_a_strong_lexical_match(self):
        # A buggy/noisy semantic_fn returning a LOW score for an already
        # exact lexical match must not downgrade it -- max(), not a blend.
        gen = [Cue(0.0, 2.0, "Hello there.")]
        ref = [Cue(0.0, 2.0, "Hello there.")]
        result = align(gen, ref, semantic_fn=lambda a, b: 0.0)[0]
        self.assertEqual(cat(result), AlignmentCategory.CORRECT)

    def test_no_semantic_fn_leaves_field_none(self):
        gen = [Cue(0.0, 2.0, "Hello there.")]
        ref = [Cue(0.0, 2.0, "Hello there.")]
        result = align(gen, ref)[0]
        self.assertIsNone(result.semantic_similarity)

    def test_semantic_fn_not_called_for_dropped_or_inserted_blocks(self):
        calls = []
        def tracking_fn(a, b):
            calls.append((a, b))
            return 0.5
        gen = [Cue(0.0, 2.0, "hello there friend")]
        ref = [Cue(0.0, 2.0, "hello there friend"), Cue(4.0, 6.0, "unrelated aardvark content")]
        align(gen, ref, semantic_fn=tracking_fn)
        # Only the real matched block should ever reach semantic_fn -- an
        # empty-sided (dropped/inserted) block has no text pair to embed.
        self.assertEqual(len(calls), 1)


class StructuralTests(unittest.TestCase):
    def test_empty_generated_is_all_dropped(self):
        ref = [Cue(0.0, 2.0, "hello"), Cue(2.0, 4.0, "world")]
        results = align([], ref)
        self.assertTrue(all(cat(r) == AlignmentCategory.DROPPED_UTTERANCE for r in results))
        self.assertEqual(sum(len(r.ref_cues) for r in results), 2)

    def test_empty_reference_is_all_inserted(self):
        gen = [Cue(0.0, 2.0, "hello"), Cue(2.0, 4.0, "world")]
        results = align(gen, [])
        self.assertTrue(all(cat(r) == AlignmentCategory.INSERTED_CONTENT for r in results))
        self.assertEqual(sum(len(r.gen_cues) for r in results), 2)

    def test_both_empty(self):
        self.assertEqual(align([], []), [])

    def test_monotonic_coverage_every_cue_accounted_for_exactly_once(self):
        gen = [Cue(float(i), float(i) + 1, f"gen {i}") for i in range(10)]
        ref = [Cue(float(i), float(i) + 1, f"ref {i}") for i in range(10)]
        results = align(gen, ref)
        gen_covered = []
        ref_covered = []
        for r in results:
            gen_covered.extend(range(r.gen_range[0], r.gen_range[1]))
            ref_covered.extend(range(r.ref_range[0], r.ref_range[1]))
        self.assertEqual(gen_covered, list(range(10)))
        self.assertEqual(ref_covered, list(range(10)))

    def test_summarize_counts_categories(self):
        gen = [Cue(0.0, 2.0, "Hello there.")]
        ref = [Cue(0.0, 2.0, "Hello there.")]
        results = align(gen, ref)
        self.assertEqual(summarize(results), {"CORRECT": 1})


if __name__ == "__main__":
    unittest.main()
