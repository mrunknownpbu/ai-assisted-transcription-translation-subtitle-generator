"""Cast metadata -> evidence-gated, episode-scoped, case-sensitive name
protection (2026-09-28). Real case behind every piece: in Love Is In The
Air "Deniz" is a character in S01E29-E37 and the word for "sea" elsewhere;
Kiraz (cherry), Balca, Melek (angel) and Sevda (love) were all mistranslated
as words when unprotected."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import cast_enrichment as ce
import cast_metadata as cm
import glossary_profile as gp
from glossary import Entity, build_glossary, protect


class ParseCharacterTests(unittest.TestCase):
    def test_nickname_titles_and_non_characters(self):
        self.assertEqual(cm.parse_character('Melek Yücel "Melo"'), ("Melek", "Melek Yücel", ["Melo"]))
        self.assertEqual(cm.parse_character("Chef Alexander Zucco"), ("Alexander", "Alexander Zucco", []))
        self.assertEqual(cm.parse_character("Fikret Bey"), ("Fikret", "Fikret", []))
        # TVmaze's Turkish title (live data, 2026-09-28): "Şef" is not a name.
        self.assertEqual(cm.parse_character("Şef Alexander Zucco"), ("Alexander", "Alexander Zucco", []))
        self.assertEqual(cm.parse_character("Eda (voice)"), ("Eda", "Eda", []))
        for raw in ("", None, "Self", "Himself", "Narrator (voice)"):
            self.assertIsNone(cm.parse_character(raw))

    def test_fold_ignores_turkish_diacritics_and_dotless_i(self):
        self.assertEqual(cm.fold("Pırıl Şimşek"), cm.fold("Piril Simsek"))
        self.assertEqual(cm.fold("İlker"), cm.fold("ilker"))


class CastBookTests(unittest.TestCase):
    def test_merges_sources_on_given_name_and_prefers_diacritics(self):
        book = cm.CastBook()
        book.add("Deniz Karsu", "tmdb", (1, 29))
        book.add("Deniz Saraçhan", "tvdb", (1, 30))
        book.add("Piril Baytekin", "imdb", (1, 1))
        book.add("Pırıl Baytekin", "tmdb", (1, 2))
        book.add("Pırıl Baytekin", "nfo", None)
        deniz, piril = book.members["deniz"], book.members[cm.fold("Pırıl")]
        self.assertEqual(deniz.sources, {"tmdb", "tvdb"})
        self.assertEqual(deniz.episodes, {(1, 29), (1, 30)})
        self.assertEqual(deniz.full_names, {"Deniz Karsu", "Deniz Saraçhan"})
        self.assertEqual(piril.given, "Pırıl")
        self.assertTrue(piril.series_level)


class NameShapedTests(unittest.TestCase):
    def test_name_uses(self):
        for text in ("Gerçekten Deniz'e âşık mısın?", "Deniz, kahve yok mu?", "Deniz!",
                     "Sonra Deniz geldi.", "-Tamam. -Deniz'i ara."):
            with self.subTest(text=text):
                self.assertTrue(ce.name_shaped(text, "Deniz"))

    def test_word_uses_and_sentence_start(self):
        for text in ("Arazi tam deniz kenarında.", "Deniz kenarında yürümek iyi geliyor.",
                     "Dalgasız bir deniz.", "Denizciler geldi.", "Can sıkıntısı."):
            with self.subTest(text=text):
                self.assertFalse(ce.name_shaped(text, "Deniz") or ce.name_shaped(text, "Can"))


class ScopeTests(unittest.TestCase):
    def test_guest_scoped_with_margin_regular_series_wide(self):
        known = {1: 39}
        guest = cm.CastMember(given="Deniz", episodes={(1, e) for e in range(29, 38)})
        regular = cm.CastMember(given="Eda", episodes={(1, e) for e in range(1, 40)})
        self.assertEqual(ce.scope_for(guest, known), ["S01E29-E39"])  # 37 + 3, capped at 39
        self.assertIsNone(ce.scope_for(regular, known))
        self.assertIsNone(ce.scope_for(cm.CastMember(given="X", series_level=True), known))

    def test_scope_parsing_and_membership(self):
        ranges = gp.parse_episode_scope(["S01E29-E40", "S02E03", "garbage"])
        self.assertEqual(ranges, [(1, 29, 40), (2, 3, 3)])
        self.assertTrue(gp.in_scope(ranges, (1, 30)))
        self.assertFalse(gp.in_scope(ranges, (1, 3)))
        self.assertFalse(gp.in_scope(ranges, None))       # unknown episode: no scoped names
        self.assertTrue(gp.in_scope(None, None))          # unscoped: everywhere
        self.assertEqual(gp.parse_episode_scope(["nonsense"]), [])  # typo narrows, never widens
        self.assertEqual(gp.find_episode("Show/S01E30.mkv"), (1, 30))


class ProfileScopeTests(unittest.TestCase):
    def test_scoped_entity_only_in_its_episodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "s.yaml").write_text(yaml.safe_dump({"tvdb_id": 1, "entities": [
                {"canonical": "Deniz", "protected": True, "episodes": ["S01E29-E40"], "case_sensitive": True},
                {"canonical": "Eda", "protected": True}]}), encoding="utf-8")
            names = lambda **kw: [e.canonical for e in gp.load_profile(tmp, tvdb_id=1, enrich_from_tvdb=False, **kw).entities]
            self.assertEqual(names(episode=(1, 30)), ["Deniz", "Eda"])
            self.assertEqual(names(episode=(1, 4)), ["Eda"])
            self.assertEqual(names(), ["Eda"])
            self.assertEqual(names(all_episodes=True), ["Deniz", "Eda"])
            deniz = gp.load_profile(tmp, tvdb_id=1, enrich_from_tvdb=False, all_episodes=True).entities[0]
            self.assertTrue(deniz.case_sensitive)
            self.assertEqual(deniz.episodes, ["S01E29-E40"])


class CaseSensitiveProtectTests(unittest.TestCase):
    def test_only_the_capitalised_name_is_protected(self):
        g = build_glossary([Entity("Melek", ["Melek", "Melo"], case_sensitive=True), Entity("Eda", ["Eda"])])
        out = protect("Melek! Bir melek gibi. Melo'yu ara. eda", g)
        self.assertNotIn("Melek", out)
        self.assertIn("melek gibi", out)
        self.assertNotIn("Melo", out)
        self.assertNotIn("eda", out)  # ordinary entries stay case-insensitive


class SourceSubtitleTests(unittest.TestCase):
    def test_hearing_impaired_english_is_not_hindi(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = "1\n00:00:01,000 --> 00:00:02,000\nMerhaba Deniz.\n\n"
            for e in range(1, 3):
                Path(tmp, f"Show S01E0{e}.tr.srt").write_text(cue, encoding="utf-8")
            for e in range(1, 4):
                Path(tmp, f"Show S01E0{e}.en.hi.srt").write_text(cue, encoding="utf-8")
                Path(tmp, f"Show S01E0{e}.en.srt").write_text(cue, encoding="utf-8")
            lang, subs = ce.source_subtitles(Path(tmp))
        self.assertEqual(lang, "tr")
        self.assertEqual(sorted(subs), [(1, 1), (1, 2)])


def _series(tmp: Path, lines_by_episode: dict[int, list[str]]) -> Path:
    root = tmp / "Show {tvdb-1}"
    root.mkdir()
    for ep, lines in lines_by_episode.items():
        body = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\n{t}\n\n" for i, t in enumerate(lines, 1))
        (root / f"Show S01E{ep:02d}.tr.srt").write_text(body, encoding="utf-8")
    return root


class EnrichSeriesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.glossary = self.tmp / "glossary"
        self.glossary.mkdir()
        (self.glossary / "show.yaml").write_text(
            "tvdb_id: 1\nentities:\n# a person wrote this\n- canonical: Eda\n  protected: true\n", encoding="utf-8")
        name_lines = {ep: [f"Sonra Kiraz geldi {ep}.", f"Kiraz'ı ara {ep}.", "Bir kiraz ye."] for ep in (5, 6, 7)}
        name_lines.update({ep: ["Eda, gel.", "Sonra Ayfer geldi."] * 3 for ep in (1, 2)})
        self.root = _series(self.tmp, name_lines)
        self.book = cm.CastBook()
        for ep in (5, 6, 7):
            self.book.add("Kiraz Yılmaz", "tmdb", (1, ep))
        for ep in range(1, 8):
            self.book.add("Ayfer Yıldız", "tmdb", (1, ep))
            self.book.add("Eda Yıldız", "imdb", (1, ep))

    def probe(self, lines, lang):
        # Kiraz is lost ("cherry"); Ayfer survives.
        return [line.replace("Kiraz'ı", "the cherry").replace("Kiraz", "Cherry") for line in lines]

    def run_enrich(self, **kw):
        # Fixture lines are too short for reliable language detection;
        # MislabelledSubtitleTests covers that check on real-length text.
        with patch.object(ce.glossary_files, "commit", return_value=True) as commit, \
             patch.object(ce, "_text_language", return_value="tr"):
            report = ce.enrich_series(1, self.root, self.glossary, probe=self.probe, book=self.book, **kw)
        return report, commit

    def test_protects_mistranslated_guest_scoped_and_case_sensitive(self):
        report, commit = self.run_enrich()
        self.assertEqual(report["added"], ["Kiraz"])
        text = (self.glossary / "show.yaml").read_text(encoding="utf-8")
        self.assertIn("# a person wrote this", text)         # comments survive
        entry = next(e for e in yaml.safe_load(text)["entities"] if e["canonical"] == "Kiraz")
        self.assertEqual(entry["episodes"], ["S01E05-E07"])  # capped at the last known episode
        self.assertTrue(entry["case_sensitive"])
        self.assertEqual(entry["source"], "metadata")
        self.assertIn("Kiraz Yılmaz", entry["aliases"])
        self.assertEqual(entry["evidence"]["unprotected_probe"], "6/6 lines lost the name")
        commit.assert_called_once()

    def test_correctly_translated_and_human_entries_are_left_alone(self):
        report, _ = self.run_enrich()
        by_name = {c["name"]: c for c in report["candidates"]}
        self.assertEqual(by_name["Ayfer"]["decision"], "skip")
        self.assertIn("translates correctly", by_name["Ayfer"]["reason"])
        self.assertIn("written by a person", by_name["Eda"]["reason"])

    def test_lowercase_word_lines_never_count_as_name_evidence(self):
        report, _ = self.run_enrich()
        kiraz = next(c for c in report["candidates"] if c["name"] == "Kiraz")
        self.assertEqual(kiraz["name_lines"], 6)  # "Bir kiraz ye." x3 excluded

    def test_rerun_updates_its_own_entry_instead_of_duplicating(self):
        self.run_enrich()
        self.run_enrich()
        names = [e["canonical"] for e in yaml.safe_load((self.glossary / "show.yaml").read_text())["entities"]]
        self.assertEqual(names.count("Kiraz"), 1)

    def test_worker_write_preserves_a_manual_edit_made_during_probe(self):
        def edit_during_probe(lines, lang):
            with ce.glossary_files.edit_lock(self.glossary):
                path = self.glossary / "show.yaml"
                data = ce.glossary_files.load(path)
                data["entities"].append({"canonical": "Melek", "protected": True})
                ce.glossary_files.write(path, data)
            return self.probe(lines, lang)

        with patch.object(ce.glossary_files, "commit", return_value=True), \
             patch.object(ce, "_text_language", return_value="tr"):
            ce.enrich_series(1, self.root, self.glossary, probe=edit_during_probe, book=self.book)
        names = {e["canonical"] for e in yaml.safe_load(
            (self.glossary / "show.yaml").read_text(encoding="utf-8"))["entities"]}
        self.assertEqual(names, {"Eda", "Melek", "Kiraz"})

    def test_dry_run_writes_nothing(self):
        before = (self.glossary / "show.yaml").read_text()
        report, commit = self.run_enrich(dry_run=True)
        self.assertEqual(report["added"], ["Kiraz"])
        self.assertEqual((self.glossary / "show.yaml").read_text(), before)
        commit.assert_not_called()

    def test_flags_series_wide_human_entry_that_metadata_scopes(self):
        (self.glossary / "show.yaml").write_text(
            "tvdb_id: 1\nentities:\n- canonical: Kiraz\n  protected: true\n", encoding="utf-8")
        report, _ = self.run_enrich()
        self.assertEqual(report["added"], [])
        self.assertTrue(any("Kiraz" in f and "S01E05-E07" in f for f in report["flags"]))


class StalenessTests(unittest.TestCase):
    def test_refresh_days_and_staleness(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cm, "CACHE_DIR", Path(tmp)):
            with patch.dict("os.environ", {"SUBTITLE_AI_CAST_REFRESH_DAYS": ""}):
                self.assertTrue(ce.is_stale(7, now=1000.0))
                ce.save_report({"tvdb_id": 7, "checked_at": 1000.0})
                self.assertFalse(ce.is_stale(7, now=1000.0 + 11 * 3600))
                self.assertTrue(ce.is_stale(7, now=1000.0 + 13 * 3600))
            with patch.dict("os.environ", {"SUBTITLE_AI_CAST_REFRESH_DAYS": "0"}):
                self.assertFalse(ce.is_stale(8))

    def test_new_episode_retries_only_an_insufficient_evidence_report(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cm, "CACHE_DIR", Path(tmp)):
            root = _series(Path(tmp), {1: ["Merhaba."], 2: ["Merhaba."]})
            report = {
                "tvdb_id": 7, "checked_at": 1000.0, "episodes_with_subtitles": 1,
                "candidates": [{"decision": "skip", "name_lines": 8, "name_episodes": 1}],
            }
            ce.save_report(report)
            self.assertTrue(ce.needs_evidence_refresh(7, root, now=1001.0))
            report["candidates"][0]["name_episodes"] = 2
            ce.save_report(report)
            self.assertFalse(ce.needs_evidence_refresh(7, root, now=1001.0))

    def test_new_episode_does_not_retry_a_report_without_evidence_gate_skip(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cm, "CACHE_DIR", Path(tmp)):
            root = _series(Path(tmp), {1: ["Merhaba."], 2: ["Merhaba."]})
            ce.save_report({"tvdb_id": 7, "checked_at": 1000.0, "episodes_with_subtitles": 1,
                            "candidates": [{"decision": "skip", "name_lines": 0, "name_episodes": 0}]})
            self.assertFalse(ce.needs_evidence_refresh(7, root, now=1001.0))


if __name__ == "__main__":
    unittest.main()


class MislabelledSubtitleTests(unittest.TestCase):
    def test_file_whose_text_is_not_its_labelled_language_is_skipped(self):
        # Real case: Veer-Zaara's ".hi.srt" is English (hearing-impaired).
        with tempfile.TemporaryDirectory() as tmp:
            english = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\nThe valley is filled with the season of love and memories.\n\n" for i in range(1, 9))
            Path(tmp, "Film (2004).hi.srt").write_text(english, encoding="utf-8")
            lang, subs = ce.source_subtitles(Path(tmp), movie=True)
        self.assertIsNone(lang)
        self.assertEqual(subs, {})

    def test_real_language_text_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            turkish = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\nBugün çok yoruldum, eve gidip biraz dinlenmek istiyorum.\n\n" for i in range(1, 9))
            Path(tmp, "Show S01E01.tr.srt").write_text(turkish, encoding="utf-8")
            lang, subs = ce.source_subtitles(Path(tmp))
        self.assertEqual((lang, list(subs)), ("tr", [(1, 1)]))


class NonLatinCastEnrichmentTests(unittest.TestCase):
    def test_parse_character_with_non_latin_parentheses_and_titles(self):
        self.assertEqual(cm.parse_character("Tachibana (立花)"), ("Tachibana", "Tachibana", ["立花"]))
        self.assertEqual(cm.parse_character("Kanako (カナコ)"), ("Kanako", "Kanako", ["カナコ"]))
        # Hyphenated honorifics stay attached (word-boundary stripping only);
        # spaced ones are stripped via _SUFFIX_TITLES.
        self.assertEqual(cm.parse_character("Tanaka san"), ("Tanaka", "Tanaka", []))
        self.assertEqual(cm.parse_character("Lee nim"), ("Lee", "Lee", []))

    def test_name_shaped_non_latin_scripts(self):
        # Katakana in Japanese dialogue
        self.assertTrue(ce.name_shaped("カナコ、早く来て！", "カナコ"))
        self.assertTrue(ce.name_shaped("(ｶﾅｺ) え？", "ｶﾅｺ"))
        # Katakana substring within longer katakana word should not match
        self.assertFalse(ce.name_shaped("チョコレート", "チョコレ"))
        
        # Hangul in Korean dialogue
        self.assertTrue(ce.name_shaped("태수 씨, 어디 가요?", "태수"))
        
        # Hanzi in Chinese dialogue
        self.assertTrue(ce.name_shaped("长庚，你快走！", "长庚"))

    def test_enrich_series_protects_non_latin_cast(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            glossary = tmp_path / "glossary"
            glossary.mkdir()
            
            # Series root with Japanese subtitles
            root = tmp_path / "Show {tvdb-99}"
            root.mkdir()
            for ep in (1, 2):
                lines = [
                    "カナコ、スマイルに行こう。",
                    "カナコが待っている。",
                    "カナコはどこ？",
                    "カナコ、早く！",
                    "カナコを探して。",
                ]
                cues = "".join(
                    f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\n{line}\n\n"
                    for i, line in enumerate(lines, 1)
                )
                (root / f"Show S01E{ep:02d}.ja.srt").write_text(cues, encoding="utf-8")
            
            book = cm.CastBook()
            book.add("Kanako (カナコ)", "tmdb", (1, 1))
            book.add("Kanako (カナコ)", "tmdb", (1, 2))
            
            # Probe simulation: unprotected translation loses the name (translates to "she" or misses it)
            def probe(lines, lang):
                return ["Let's go to smile." for _ in lines]
            
            with patch.object(ce.glossary_files, "commit", return_value=True), \
                 patch.object(ce, "_text_language", return_value="ja"):
                report = ce.enrich_series(99, root, glossary, probe=probe, book=book)
            
            self.assertEqual(report["added"], ["Kanako"])
            entry = yaml.safe_load((glossary / "tvdb-99.yaml").read_text())["entities"][0]
            self.assertEqual(entry["canonical"], "Kanako")
            self.assertIn("カナコ", entry["aliases"])
            self.assertTrue(entry["protected"])
