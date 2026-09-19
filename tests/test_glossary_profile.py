import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from glossary_profile import find_series_glossary_path, find_series_root, find_tvdb_id, load_profile

GLOBAL_YAML = """
entities:
  - canonical: abi
    type: term
    protected: false
    aliases: []
"""

CATEGORY_YAML = """
entities:
  - canonical: Petunya
    type: term
    protected: true
    aliases:
      - Petunia
"""

SERIES_YAML = """
tvdb_id: 383383
title: "Test Series"
entities:
  - canonical: Eda Yıldız
    type: character
    protected: true
    aliases:
      - Eda Yildiz
      - Eda
  - canonical: Petunya
    type: term
    protected: true
    aliases: []
"""


class LoadProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "global-tr-en.yaml").write_text(GLOBAL_YAML, encoding="utf-8")
        (self.dir / "turkish-drama-tr-en.yaml").write_text(CATEGORY_YAML, encoding="utf-8")
        (self.dir / "test-series-tr-en.yaml").write_text(SERIES_YAML, encoding="utf-8")

    def test_only_protected_entities_included(self):
        profile = load_profile(self.dir, tvdb_id=383383)
        canonicals = {e.canonical for e in profile.entities}
        self.assertIn("Petunya", canonicals)
        self.assertNotIn("abi", canonicals)

    def test_series_layer_included_when_tvdb_id_matches(self):
        profile = load_profile(self.dir, tvdb_id=383383)
        canonicals = {e.canonical for e in profile.entities}
        self.assertIn("Eda Yıldız", canonicals)
        self.assertEqual(profile.title, "Test Series")

    def test_series_layer_excluded_when_tvdb_id_does_not_match(self):
        profile = load_profile(self.dir, tvdb_id=999999)
        canonicals = {e.canonical for e in profile.entities}
        self.assertNotIn("Eda Yıldız", canonicals)
        self.assertIn("Petunya", canonicals)

    def test_aliases_become_surface_forms(self):
        profile = load_profile(self.dir, tvdb_id=383383)
        eda = next(e for e in profile.entities if e.canonical == "Eda Yıldız")
        self.assertIn("Eda", eda.surface_forms)
        self.assertIn("Eda Yildiz", eda.surface_forms)

    def test_sources_recorded(self):
        profile = load_profile(self.dir, tvdb_id=383383)
        self.assertEqual(len(profile.sources), 3)

    def test_no_tvdb_id_uses_only_global_and_category_layers(self):
        profile = load_profile(self.dir, tvdb_id=None)
        canonicals = {e.canonical for e in profile.entities}
        self.assertNotIn("Eda Yıldız", canonicals)
        self.assertIn("Petunya", canonicals)


class FindTvdbIdTests(unittest.TestCase):
    def test_extracts_id_from_ancestor_directory_tag(self):
        path = "/data/drama/turkish/Love Is In The Air (2020) {tvdb-383383}/Season 01/S01E01.mkv"
        self.assertEqual(find_tvdb_id(path), 383383)

    def test_no_tag_returns_none(self):
        self.assertIsNone(find_tvdb_id("/data/drama/turkish/Some Show/Season 01/S01E01.mkv"))


class FindSeriesRootTests(unittest.TestCase):
    def test_finds_ancestor_directory_matching_tvdb_pattern(self):
        path = "/data/drama/turkish/Love Is In The Air (2020) {tvdb-383383}/Season 01/S01E01.mkv"
        root = find_series_root(path)
        self.assertEqual(root.name, "Love Is In The Air (2020) {tvdb-383383}")

    def test_returns_none_when_no_tvdb_tag_in_path(self):
        self.assertIsNone(find_series_root("/data/drama/turkish/Some Show/Season 01/S01E01.mkv"))

    def test_returns_nearest_matching_ancestor(self):
        # A coincidental further ancestor also matching must not win over
        # the nearer, correct series root.
        path = ("/data/{tvdb-999999}/drama/Love Is In The Air (2020) {tvdb-383383}"
               "/Season 01/S01E01.mkv")
        root = find_series_root(path)
        self.assertEqual(root.name, "Love Is In The Air (2020) {tvdb-383383}")


class FindSeriesGlossaryPathTests(unittest.TestCase):
    def test_finds_file_matching_tvdb_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "show.yaml"
            path.write_text("tvdb_id: 383383\ntitle: Show\nentities: []\n", encoding="utf-8")
            self.assertEqual(find_series_glossary_path(tmp, 383383), path)

    def test_returns_none_when_no_file_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "global.yaml").write_text("entities: []\n", encoding="utf-8")
            self.assertIsNone(find_series_glossary_path(tmp, 999999))

    def test_ignores_global_layer_with_no_tvdb_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "global.yaml").write_text("entities: []\n", encoding="utf-8")
            self.assertIsNone(find_series_glossary_path(tmp, 383383))


TURKISH_PHRASE_YAML = """
language: tr
phrases:
  - source: "Peki."
    translation: "Okay."
  - source: "Bekle."
    translation: "Wait."
"""

SERIES_PHRASE_OVERRIDE_YAML = """
tvdb_id: 383383
title: "Test Series"
phrases:
  - source: "Peki."
    translation: "Alright, boss."
"""


class LoadProfilePhrasesTests(unittest.TestCase):
    """phrases: (added 2026-09-19, see glossary.PhraseEntry) -- additive
    to the existing entities: loading, same layering rules (global/
    category layers, keyed by file `language:`, then series layer wins)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_phrases_loaded_from_no_tvdb_id_file(self):
        (self.dir / "turkish.yaml").write_text(TURKISH_PHRASE_YAML, encoding="utf-8")
        profile = load_profile(self.dir, tvdb_id=None)
        by_source = {p.source: p.translation for p in profile.phrases}
        self.assertEqual(by_source["Peki."], "Okay.")
        self.assertEqual(by_source["Bekle."], "Wait.")

    def test_file_level_language_attached_to_every_phrase_from_that_file(self):
        (self.dir / "turkish.yaml").write_text(TURKISH_PHRASE_YAML, encoding="utf-8")
        profile = load_profile(self.dir, tvdb_id=None)
        self.assertTrue(all(p.language == "tr" for p in profile.phrases))

    def test_no_language_key_means_universal_phrase(self):
        (self.dir / "global.yaml").write_text('phrases:\n  - source: "OK"\n    translation: "OK"\n',
                                               encoding="utf-8")
        profile = load_profile(self.dir, tvdb_id=None)
        self.assertIsNone(profile.phrases[0].language)

    def test_series_layer_phrase_overrides_same_key_from_earlier_layer(self):
        (self.dir / "turkish.yaml").write_text(TURKISH_PHRASE_YAML, encoding="utf-8")
        (self.dir / "series.yaml").write_text(SERIES_PHRASE_OVERRIDE_YAML, encoding="utf-8")
        profile = load_profile(self.dir, tvdb_id=383383)
        by_source = {p.source: p.translation for p in profile.phrases}
        self.assertEqual(by_source["Peki."], "Alright, boss.")
        # The un-overridden phrase from the earlier layer is untouched.
        self.assertEqual(by_source["Bekle."], "Wait.")

    def test_series_layer_not_applied_when_tvdb_id_does_not_match(self):
        (self.dir / "turkish.yaml").write_text(TURKISH_PHRASE_YAML, encoding="utf-8")
        (self.dir / "series.yaml").write_text(SERIES_PHRASE_OVERRIDE_YAML, encoding="utf-8")
        profile = load_profile(self.dir, tvdb_id=999999)
        by_source = {p.source: p.translation for p in profile.phrases}
        self.assertEqual(by_source["Peki."], "Okay.")


class TvdbEnrichmentTests(unittest.TestCase):
    """A local title always wins outright -- TVDB is queried only to fill
    a genuine gap, never to override what the glossary YAML already says."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "global-tr-en.yaml").write_text(GLOBAL_YAML, encoding="utf-8")
        (self.dir / "turkish-drama-tr-en.yaml").write_text(CATEGORY_YAML, encoding="utf-8")
        # No local title -- these tests observe whether enrichment fills the gap.
        (self.dir / "test-series-tr-en.yaml").write_text(
            SERIES_YAML.replace('title: "Test Series"\n', ""), encoding="utf-8")

    def test_tvdb_queried_when_local_title_missing(self):
        with patch("tvdb_client.series", return_value={"name": "From TVDB"}) as series:
            profile = load_profile(self.dir, tvdb_id=383383)
        series.assert_called_once_with(383383)
        self.assertEqual(profile.title, "From TVDB")
        self.assertEqual(profile.title_source, "tvdb")

    def test_local_title_never_overridden_by_tvdb(self):
        (self.dir / "test-series-tr-en.yaml").write_text(SERIES_YAML, encoding="utf-8")  # restore local title
        with patch("tvdb_client.series") as series:
            profile = load_profile(self.dir, tvdb_id=383383)
        series.assert_not_called()
        self.assertEqual(profile.title, "Test Series")
        self.assertEqual(profile.title_source, "local")

    def test_enrich_from_tvdb_false_never_calls_out(self):
        with patch("tvdb_client.series") as series:
            profile = load_profile(self.dir, tvdb_id=383383, enrich_from_tvdb=False)
        series.assert_not_called()
        self.assertIsNone(profile.title)
        self.assertEqual(profile.title_source, "none")

    def test_no_match_leaves_title_none_not_invented(self):
        with patch("tvdb_client.series", return_value=None):
            profile = load_profile(self.dir, tvdb_id=383383)
        self.assertIsNone(profile.title)
        self.assertEqual(profile.title_source, "none")

    def test_no_tvdb_id_never_calls_out(self):
        with patch("tvdb_client.series") as series:
            profile = load_profile(self.dir, tvdb_id=None)
        series.assert_not_called()


class RealGlossaryDataTests(unittest.TestCase):
    """Read-only against the actual production glossary files, if present
    in this environment -- proves the loader works on real data, not just
    synthetic fixtures. Skips gracefully if unavailable."""

    REAL_DIR = Path("/opt/docker/appdata/subtitle-ai/glossary")

    def test_loads_real_series_profile(self):
        if not self.REAL_DIR.is_dir():
            self.skipTest("real glossary directory not present in this environment")
        profile = load_profile(self.REAL_DIR, tvdb_id=383383)
        canonicals = {e.canonical for e in profile.entities}
        self.assertIn("Eda Yıldız", canonicals)
        self.assertIn("Serkan Bolat", canonicals)
        eda = next(e for e in profile.entities if e.canonical == "Eda Yıldız")
        self.assertIn("Eda", eda.surface_forms)


if __name__ == "__main__":
    unittest.main()
