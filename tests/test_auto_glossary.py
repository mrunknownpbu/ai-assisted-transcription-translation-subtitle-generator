"""auto_glossary.py -- mining recurring proper names from a series' own
already-completed sibling episode outputs (see module docstring there
for the safety framing: hotwords-only, never translation protection)."""

import tempfile
import unittest
from pathlib import Path

import yaml

from auto_glossary import AutoCandidate, mine_series_entities, write_suggestions


def _write_pair(root: Path, stem: str, tr_lines: list[str], *, with_english=True):
    tr_cues = "\n\n".join(
        f"{i}\n00:00:{i:02d},000 --> 00:00:{i+1:02d},000\n{line}"
        for i, line in enumerate(tr_lines, 1))
    (root / f"{stem}.tr.srt").write_text(tr_cues, encoding="utf-8")
    if with_english:
        (root / f"{stem}.en.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nSome translation.", encoding="utf-8")


class MiningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_mines_recurring_name_across_multiple_episodes(self):
        lines = ["Eda geldi.", "Nerede Eda?", "Eda çok mutlu."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines)
        candidates = mine_series_entities(self.root)
        self.assertIn("Eda", [c.canonical for c in candidates])

    def test_below_min_occurrences_per_episode_not_counted(self):
        lines = ["Eda geldi.", "Başka bir cümle burada."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines)
        candidates = mine_series_entities(self.root)
        self.assertNotIn("Eda", [c.canonical for c in candidates])

    def test_single_episode_is_sufficient(self):
        # MIN_DISTINCT_EPISODES=1 (lowered 2026-09-19, real request: a
        # genuine character name shouldn't have to wait for 3 episodes
        # before it's even surfaced as a promotable suggestion).
        lines = ["Eda geldi.", "Nerede Eda?", "Eda çok mutlu."]
        _write_pair(self.root, "S01E01", lines)
        candidates = mine_series_entities(self.root)
        self.assertIn("Eda", [c.canonical for c in candidates])

    def test_all_caps_tokens_excluded(self):
        lines = ["EDA geldi.", "EDA nerede?", "EDA çok mutlu."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines)
        candidates = mine_series_entities(self.root)
        self.assertNotIn("EDA", [c.canonical for c in candidates])

    def test_stopwords_excluded(self):
        lines = ["Tamam geldim.", "Tamam gidiyorum.", "Anne bak.", "Anne gel."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines)
        candidates = [c.canonical for c in mine_series_entities(self.root)]
        self.assertNotIn("Tamam", candidates)
        self.assertNotIn("Anne", candidates)

    def test_apostrophe_suffixed_inflections_merge_into_base_form(self):
        lines = ["Eda'yı gördüm.", "Onu Eda'ya söyledim.", "Eda'nın evi burada."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines)
        candidates = mine_series_entities(self.root)
        self.assertIn("Eda", [c.canonical for c in candidates])

    def test_requires_paired_english_sibling_to_exist(self):
        lines = ["Eda geldi.", "Nerede Eda?", "Eda çok mutlu."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, with_english=False)
        candidates = mine_series_entities(self.root)
        self.assertEqual(candidates, [])

    def test_excludes_current_job_srt_from_mining(self):
        lines = ["Eda geldi.", "Nerede Eda?", "Eda çok mutlu."]
        _write_pair(self.root, "S01E01", lines)
        exclude = self.root / "S01E01.tr.srt"
        candidates = mine_series_entities(self.root, exclude_srt_path=exclude)
        # The only qualifying episode was excluded -- zero remain.
        self.assertNotIn("Eda", [c.canonical for c in candidates])

    def test_exclude_canonicals_filtered_during_counting(self):
        lines = ["Eda geldi.", "Nerede Eda?", "Eda çok mutlu.",
                "Serkan geldi.", "Nerede Serkan?", "Serkan çok mutlu."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines)
        candidates = [c.canonical for c in
                     mine_series_entities(self.root, exclude_canonicals={"eda"})]
        self.assertNotIn("Eda", candidates)
        self.assertIn("Serkan", candidates)

    def test_corrupt_sibling_file_does_not_abort_other_episodes(self):
        lines = ["Eda geldi.", "Nerede Eda?", "Eda çok mutlu."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines)
        (self.root / "S01E99.tr.srt").write_bytes(b"\xff\xfe\x00garbage")
        (self.root / "S01E99.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nx",
                                                  encoding="utf-8")
        candidates = mine_series_entities(self.root)
        self.assertIn("Eda", [c.canonical for c in candidates])

    def test_no_matches_returns_empty_list(self):
        self.assertEqual(mine_series_entities(self.root), [])

    def test_sentence_initial_only_capitalization_not_counted_as_proper_noun(self):
        # "Bir" ("a"/"one") clears both MIN_OCCURRENCES_PER_EPISODE and
        # MIN_DISTINCT_EPISODES on real production data purely because
        # Turkish orthography capitalizes the first word of every cue --
        # not because it's a name. It must never survive unless it also
        # appears capitalized somewhere NOT at the start of a cue.
        lines = ["Bir dakika lütfen.", "Bir şey söyleyeceğim.", "Bir dahaki sefere."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines)
        candidates = [c.canonical for c in mine_series_entities(self.root)]
        self.assertNotIn("Bir", candidates)


class WriteSuggestionsTests(unittest.TestCase):
    def test_output_matches_real_glossary_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            cands = [AutoCandidate(canonical="Eda",
                                   episode_counts={"a": 3, "b": 3, "c": 3}, total_count=9)]
            path = write_suggestions(tmp, 383383, cands, title="Test Series")
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(data["tvdb_id"], 383383)
            self.assertEqual(data["title"], "Test Series")
            entity = data["entities"][0]
            self.assertEqual(entity["canonical"], "Eda")
            self.assertEqual(entity["aliases"], [])
            self.assertFalse(entity["protected"])


if __name__ == "__main__":
    unittest.main()
