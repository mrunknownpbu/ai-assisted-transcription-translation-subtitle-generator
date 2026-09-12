import unittest

from projection import ProjectedCue, SourceGroup, merge_groups, project, validate_coverage
from qc.types import QcCategory
from transcript import BoundaryReason, Segment, Word


def source_cue(index, start, end, text, boundary=None):
    words = [Word(text=w, original_text=w, start=start, end=end) for w in text.split()]
    return Segment(index=index, start=start, end=end, words=words, avg_logprob=0.0,
                  no_speech_prob=0.0, compression_ratio=0.0, boundary_before=boundary)


def pcue(start, end, text, group_index=0, source_indices=None):
    return ProjectedCue(start, end, [text], group_index, source_indices or [])


class MergeGroupsTests(unittest.TestCase):
    def test_no_boundary_provenance_is_one_group_per_cue(self):
        cues = [source_cue(0, 0.0, 1.0, "a"), source_cue(1, 1.0, 2.0, "b")]
        groups = merge_groups(cues)
        self.assertEqual([g.indices for g in groups], [[0], [1]])

    def test_display_split_boundary_merges(self):
        cues = [source_cue(0, 0.0, 1.0, "a"),
               source_cue(1, 1.05, 2.0, "b", boundary=BoundaryReason.DISPLAY_SPLIT)]
        groups = merge_groups(cues)
        self.assertEqual([g.indices for g in groups], [[0, 1]])

    def test_real_acoustic_gap_never_merges(self):
        cues = [source_cue(0, 0.0, 1.0, "a"),
               source_cue(1, 1.05, 2.0, "b", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        groups = merge_groups(cues)
        self.assertEqual([g.indices for g in groups], [[0], [1]])

    def test_merge_rejected_past_max_duration(self):
        cues = [source_cue(0, 0.0, 4.0, "a"),
               source_cue(1, 4.05, 8.0, "b", boundary=BoundaryReason.DISPLAY_SPLIT)]
        groups = merge_groups(cues)
        self.assertEqual([g.indices for g in groups], [[0], [1]])

    def test_three_way_chain_merges(self):
        # The real confirmed production shape: TR cues 66/67/68 merged.
        cues = [
            source_cue(0, 245.671, 249.777, "a"),
            source_cue(1, 249.777, 250.19, "b", boundary=BoundaryReason.DISPLAY_SPLIT),
            source_cue(2, 250.19, 251.19, "c", boundary=BoundaryReason.MAX_LENGTH),
        ]
        groups = merge_groups(cues)
        self.assertEqual([g.indices for g in groups], [[0, 1, 2]])


class ValidateCoverageTests(unittest.TestCase):
    def test_plain_1_to_1_valid(self):
        cues = [source_cue(0, 0.0, 1.0, "a")]
        groups = merge_groups(cues)
        projected = [pcue(0.0, 1.0, "A", 0, [0])]
        result = validate_coverage(cues, groups, projected)
        self.assertEqual(result.flagged, 0)

    def test_n_to_1_merge_valid(self):
        cues = [source_cue(0, 0.0, 1.0, "a"),
               source_cue(1, 1.05, 2.0, "b", boundary=BoundaryReason.DISPLAY_SPLIT)]
        groups = merge_groups(cues)
        projected = [pcue(0.0, 2.0, "A B", 0, [0, 1])]
        result = validate_coverage(cues, groups, projected)
        self.assertEqual(result.flagged, 0)

    def test_1_to_n_split_valid(self):
        cues = [source_cue(0, 0.0, 10.0, "a")]
        groups = merge_groups(cues)
        projected = [pcue(0.0, 5.0, "A1", 0, [0]), pcue(5.0, 10.0, "A2", 0, [0])]
        result = validate_coverage(cues, groups, projected)
        self.assertEqual(result.flagged, 0)

    def test_n_to_m_merge_then_split_valid(self):
        # The exact production regression: 3 merged source cues, split
        # into 2 target cues whose internal boundary does not land on any
        # individual source cue's end.
        cues = [
            source_cue(0, 245.671, 249.777, "a"),
            source_cue(1, 249.777, 250.19, "b", boundary=BoundaryReason.DISPLAY_SPLIT),
            source_cue(2, 250.19, 251.19, "c", boundary=BoundaryReason.MAX_LENGTH),
        ]
        groups = merge_groups(cues)
        projected = [pcue(245.671, 250.883, "part one", 0, [0, 1, 2]),
                    pcue(250.883, 251.19, "dreams.", 0, [0, 1, 2])]
        result = validate_coverage(cues, groups, projected)
        self.assertEqual(result.flagged, 0)

    def test_dropped_middle_cue_rejected(self):
        cues = [source_cue(0, 0.0, 1.0, "a"), source_cue(1, 1.0, 2.0, "b"),
               source_cue(2, 2.0, 3.0, "c")]
        groups = merge_groups(cues)
        projected = [pcue(0.0, 1.0, "A", 0, [0]), pcue(2.0, 3.0, "C", 2, [2])]
        result = validate_coverage(cues, groups, projected)
        self.assertEqual(result.flagged, 1)
        self.assertEqual(result.findings[0].category, QcCategory.DROPPED_UTTERANCE)
        self.assertEqual(result.findings[0].index, 1)

    def test_backward_mapping_rejected(self):
        cues = [source_cue(0, 0.0, 1.0, "a"), source_cue(1, 1.0, 2.0, "b")]
        groups = merge_groups(cues)
        projected = [pcue(1.0, 2.0, "B", 1, [1])]   # skips cue 0
        result = validate_coverage(cues, groups, projected)
        self.assertEqual(result.flagged, 1)
        self.assertEqual(result.findings[0].index, 0)

    def test_unrelated_target_mapping_rejected(self):
        cues = [source_cue(0, 0.0, 1.0, "a")]
        groups = merge_groups(cues)
        projected = [pcue(50.0, 51.0, "unrelated", 0, [0])]
        result = validate_coverage(cues, groups, projected)
        self.assertEqual(result.flagged, 1)

    def test_zero_duration_group_coinciding_with_prior_group_end_still_covered(self):
        # Real production bug, found only by a full-episode (2h08m) run:
        # a zero-width ASR word timestamp produced a zero-duration source
        # cue whose group.end (10.0) exactly coincided with the PRIOR
        # group's cue.end (also 10.0). The old timing-inference walk let
        # that one prior cue satisfy `cue.end >= groups[k].end` for BOTH
        # groups, silently absorbing the zero-duration group's coverage
        # without ever consuming its own dedicated (zero-duration) cue --
        # desyncing the walk and misreporting a later, unrelated group as
        # dropped. Group identity must come from source_group_index, not
        # from this kind of timing coincidence.
        cues = [source_cue(0, 9.0, 10.0, "robot"),
               source_cue(1, 10.0, 10.0, "robot"),      # zero-duration
               source_cue(2, 11.0, 11.5, "dogru")]
        groups = merge_groups(cues)
        self.assertEqual([g.indices for g in groups], [[0], [1], [2]])
        projected = [
            pcue(9.0, 10.0, "The robot", 0, [0]),
            pcue(10.0, 10.0, "The robot", 1, [1]),       # its own zero-duration cue
            pcue(11.0, 11.5, "That's right.", 2, [2]),
        ]
        result = validate_coverage(cues, groups, projected)
        self.assertEqual(result.flagged, 0, result.findings)

    def test_missing_zero_duration_group_cue_still_detected_as_dropped(self):
        # The flip side: if the zero-duration group's cue is genuinely
        # absent (not merely coincidental timing), that must still be
        # caught -- provenance-based tracking must not become blind to
        # a real drop just because it stopped inferring from timing.
        cues = [source_cue(0, 9.0, 10.0, "robot"),
               source_cue(1, 10.0, 10.0, "robot"),
               source_cue(2, 11.0, 11.5, "dogru")]
        groups = merge_groups(cues)
        projected = [
            pcue(9.0, 10.0, "The robot", 0, [0]),
            # group 1's cue is missing entirely
            pcue(11.0, 11.5, "That's right.", 2, [2]),
        ]
        result = validate_coverage(cues, groups, projected)
        self.assertEqual(result.flagged, 1)
        self.assertEqual(result.findings[0].category, QcCategory.DROPPED_UTTERANCE)
        self.assertEqual(result.findings[0].index, 1)


class ProjectTests(unittest.TestCase):
    def test_flattens_with_provenance(self):
        groups = [SourceGroup([0, 1], 0.0, 2.0)]
        target = [[pcue(0.0, 1.0, "a"), pcue(1.0, 2.0, "b")]]
        projected = project(groups, target)
        self.assertEqual(len(projected), 2)
        self.assertEqual(projected[0].source_group_index, 0)
        self.assertEqual(projected[0].source_indices, [0, 1])


if __name__ == "__main__":
    unittest.main()
