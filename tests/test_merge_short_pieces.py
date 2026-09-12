"""merge_short_pieces() -- real bug this fixes: a bare recovered entity
mention ("Eda.") became its own ~0.35s orphan cue immediately before the
sentence it belonged with, found by a real 5-minute audio test.
"""

import unittest

from segmentation_target import MAX_CUE_CHARS, merge_short_pieces, segment


class MergeShortPiecesTests(unittest.TestCase):
    def test_short_leading_fragment_merges_forward(self):
        result = merge_short_pieces(["Eda.", "Take it easy, everything will be fine."])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "Eda. Take it easy, everything will be fine.")

    def test_short_trailing_fragment_merges_backward(self):
        result = merge_short_pieces(["This is a perfectly normal sentence here.", "OK."])
        self.assertEqual(len(result), 1)

    def test_two_short_fragments_in_a_row_both_absorbed(self):
        result = merge_short_pieces(["Eda.", "Eda.", "Take it easy now, please."])
        self.assertEqual(len(result), 1)
        self.assertIn("Eda. Eda.", result[0])

    def test_normal_length_pieces_left_alone(self):
        pieces = ["This is a perfectly normal first sentence here.",
                 "And a perfectly normal second sentence too."]
        self.assertEqual(merge_short_pieces(pieces), pieces)

    def test_does_not_exceed_max_chars_when_merging(self):
        long_piece = "x" * (MAX_CUE_CHARS - 5)
        result = merge_short_pieces(["short", long_piece], max_chars=MAX_CUE_CHARS)
        # Cannot merge without exceeding the cap -- left as two pieces
        # rather than violating the structural limit.
        self.assertEqual(len(result), 2)

    def test_single_piece_unchanged(self):
        self.assertEqual(merge_short_pieces(["only one"]), ["only one"])

    def test_empty_list_unchanged(self):
        self.assertEqual(merge_short_pieces([]), [])

    def test_text_conservation(self):
        pieces = ["Eda.", "Eda.", "Take it easy now."]
        merged = merge_short_pieces(pieces)
        self.assertEqual(" ".join(merged).split(), " ".join(pieces).split())


class SegmentIntegrationTests(unittest.TestCase):
    def test_recovered_entity_prefix_no_longer_produces_an_orphan_cue(self):
        # The exact real shape: recover_dropped_entities() prepends "Eda."
        # to a translation before target segmentation ever sees it.
        text = "Eda. Eda, take it easy."
        cues = segment(text, 11.180, 12.540)
        self.assertEqual(len(cues), 1)
        self.assertGreaterEqual(cues[0].end - cues[0].start, 1.0)


if __name__ == "__main__":
    unittest.main()
