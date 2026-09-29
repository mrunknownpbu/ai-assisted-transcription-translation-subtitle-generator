"""auto_glossary.py -- mining recurring proper names from a series' own
already-completed sibling episode outputs (see module docstring there
for the safety framing: hotwords-only, never translation protection)."""

import tempfile
import unittest
from pathlib import Path

import yaml

from auto_glossary import (AutoCandidate, detect_series_mining_language,
                           mine_series_entities, mine_series_entities_auto,
                           write_suggestions)


def _write_pair(root: Path, stem: str, tr_lines: list[str], *, with_english=True, lang="tr"):
    tr_cues = "\n\n".join(
        f"{i}\n00:00:{i:02d},000 --> 00:00:{i+1:02d},000\n{line}"
        for i, line in enumerate(tr_lines, 1))
    (root / f"{stem}.{lang}.srt").write_text(tr_cues, encoding="utf-8")
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

    def test_common_word_mid_cue_still_excluded_via_stopwords(self):
        # Real bug (Season 01 full-corpus mining, 2026-09-20): faster-
        # whisper's Turkish casing sometimes capitalizes ordinary function
        # words mid-cue too, not just cue-initial, so the corroboration
        # check alone let them through as if they were proper nouns and
        # they crowded real recurring character names out of
        # TOP_N_CANDIDATES. STOPWORDS is the actual backstop.
        lines = ["Gördüm Çok kötüydü.", "Aslında Çok iyiydi.", "Bence Çok komikti."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines)
        candidates = [c.canonical for c in mine_series_entities(self.root)]
        self.assertNotIn("Çok", candidates)

    def test_title_case_lyric_cue_excluded_entirely(self):
        # Real bug (Season 01, mostly S01E01-E05): the sung opening theme
        # is transcribed in Title Case, unlike ordinary dialogue -- every
        # word capitalized makes each one look like a corroborated proper
        # noun. Such a cue must contribute no candidates at all, even for
        # a token that would otherwise clear every other check.
        lyric = "Yanlislarimdan Ders Alacak Kadar Olgun Degilim Sana"
        lines = [lyric, lyric, lyric, "Pirilti bir şey söyledi.", "Pirilti geldi."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines)
        candidates = [c.canonical for c in mine_series_entities(self.root)]
        # None of the lyric's words qualify purely from lyric repetition.
        self.assertNotIn("Sana", candidates)
        self.assertNotIn("Degilim", candidates)


class JapaneseMiningTests(unittest.TestCase):
    """Katakana script-switching, not capitalization -- see auto_glossary.py's
    module docstring. Real cases below are the exact motivating ones
    (2026-09-29, "Happy Kanako's Killer Life" S02): スマイル is the show's
    talent-agency name, mistranslated as the common word "smile" because
    no glossary existed for this series at all; a Turkish-only miner
    mines nothing for a Japanese series by construction."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_mines_recurring_katakana_name_across_episodes(self):
        lines = ["カナコ です", "はい カナコ", "カナコ 待って"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ja")
        candidates = mine_series_entities(self.root, "ja")
        self.assertIn("カナコ", [c.canonical for c in candidates])

    def test_real_agency_name_recovered_from_a_single_episode(self):
        # The real motivating case: only ONE episode's dialogue mentions
        # it (3 times, clearing MIN_OCCURRENCES_PER_EPISODE), and
        # MIN_DISTINCT_EPISODES=1 already covers a single-episode name.
        lines = ["スマイル 辞めない", "スマイル より稼ごう", "スマイル は嫌だ"]
        _write_pair(self.root, "S01E03", lines, lang="ja")
        candidates = mine_series_entities(self.root, "ja")
        self.assertIn("スマイル", [c.canonical for c in candidates])

    def test_bracketed_speaker_label_prefix_stripped_not_mined(self):
        # Real content (confirmed genuinely spoken, not an ASR artifact --
        # see CLAUDE.md): a half-width-katakana speaker label prefixes
        # some cues. It must never itself become a mining candidate.
        lines = ["(ｶﾅｺ)うーん", "(ｶﾅｺ)見出すと", "(ｶﾅｺ)止まんない"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ja")
        candidates = [c.canonical for c in mine_series_entities(self.root, "ja")]
        self.assertNotIn("ｶﾅｺ", candidates)

    def test_no_position_confound_single_word_cue_still_corroborates(self):
        # Latin scripts need a NON-cue-initial sighting to corroborate
        # (see the position-confound tests below) because sentence-
        # initial capitalization is itself a confound. Katakana has no
        # such confound, so a name that ONLY ever appears cue-initial
        # (a very short, single-word line) must still qualify.
        lines = ["カズ", "カズ", "カズ"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ja")
        candidates = [c.canonical for c in mine_series_entities(self.root, "ja")]
        self.assertIn("カズ", candidates)

    def test_stopword_loanwords_excluded(self):
        # Real measured noise (2026-09-29 mining run over the only real
        # .ja.srt transcripts in this deployment): common katakana
        # loanwords/interjections that recur often but aren't names.
        lines = ["マジ で ダメ", "マジ に ダメ", "本当に マジ ダメ"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ja")
        candidates = [c.canonical for c in mine_series_entities(self.root, "ja")]
        self.assertNotIn("マジ", candidates)
        self.assertNotIn("ダメ", candidates)

    def test_single_katakana_character_too_short_to_qualify(self):
        lines = ["ア と イ", "ア また イ", "ア さらに イ"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ja")
        candidates = [c.canonical for c in mine_series_entities(self.root, "ja")]
        self.assertNotIn("ア", candidates)
        self.assertNotIn("イ", candidates)


class MalayMiningTests(unittest.TestCase):
    """Latin capitalization, same mechanism as Turkish -- see auto_glossary.py's
    module docstring for why STOPWORDS_MS is an unvalidated starter list
    (no real Malay transcript exists in this deployment yet)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_mines_recurring_name_across_episodes(self):
        lines = ["Aisyah datang.", "Mana Aisyah?", "Aisyah sangat gembira."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ms")
        candidates = mine_series_entities(self.root, "ms")
        self.assertIn("Aisyah", [c.canonical for c in candidates])

    def test_sentence_initial_only_capitalization_not_counted(self):
        lines = ["Baik saya faham.", "Baik kita pergi.", "Baik boleh juga."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ms")
        candidates = [c.canonical for c in mine_series_entities(self.root, "ms")]
        self.assertNotIn("Baik", candidates)

    def test_stopwords_excluded(self):
        lines = ["Emak tolong.", "Ya Emak datang.", "Emak ada di sini Ya."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ms")
        candidates = [c.canonical for c in mine_series_entities(self.root, "ms")]
        self.assertNotIn("Emak", candidates)

    def test_all_caps_excluded(self):
        lines = ["AISYAH datang.", "AISYAH pergi.", "AISYAH gembira."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ms")
        candidates = mine_series_entities(self.root, "ms")
        self.assertNotIn("AISYAH", [c.canonical for c in candidates])


class KoreanMiningTests(unittest.TestCase):
    """Word-spaced, but no capitalization AND no distinct-script signal --
    see auto_glossary.py's module docstring for the real cross-series
    measurement STOPWORDS_KO was built from (2026-09-29, "Confidence
    Queen" tvdb-444735 + "Not Others" tvdb-428265, extracted from these
    series' own embedded Korean subtitle streams)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_mines_recurring_name_across_episodes(self):
        lines = ["제임스가 왔다", "제임스는 어디", "제임스를 봤어"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ko")
        candidates = mine_series_entities(self.root, "ko")
        self.assertIn("제임스", [c.canonical for c in candidates])

    def test_particle_suffixed_inflections_merge_into_base_form(self):
        # 은/는/이/가/을/를 etc. attach directly with no space -- Korean's
        # agglutinative equivalent of Turkish's apostrophe suffixes.
        lines = ["전태수는 갔다", "전태수가 왔다", "전태수를 봤다"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ko")
        candidates = mine_series_entities(self.root, "ko")
        self.assertIn("전태수", [c.canonical for c in candidates])

    def test_stopword_pronouns_and_connectives_excluded(self):
        # Real measured cross-series overlap: these recur in ANY Korean
        # dialogue regardless of show, so they can never be names.
        lines = ["아니 진짜 우리", "아니 진짜 그래", "우리 그래 이거"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ko")
        candidates = [c.canonical for c in mine_series_entities(self.root, "ko")]
        self.assertNotIn("아니", candidates)
        self.assertNotIn("진짜", candidates)
        self.assertNotIn("우리", candidates)

    def test_stopword_kinship_honorific_excluded(self):
        # Hand-curated supplement, same category as STOPWORDS_TR's
        # Anne/Baba/Bey entries -- direct-address terms, not names.
        lines = ["회장님 오셨어요", "회장이 말했다", "회장은 없다"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ko")
        candidates = [c.canonical for c in mine_series_entities(self.root, "ko")]
        self.assertNotIn("회장", candidates)

    def test_single_syllable_too_short_to_qualify(self):
        lines = ["그 가 나", "그 가 나", "그 가 나"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ko")
        candidates = [c.canonical for c in mine_series_entities(self.root, "ko")]
        self.assertNotIn("그", candidates)


class ChineseMiningTests(unittest.TestCase):
    """No word spacing AND no capitalization-equivalent marker -- see
    auto_glossary.py's module docstring for the real cross-series
    measurement STOPWORDS_ZH was built from (2026-09-29, "Pull Strings"
    tvdb-467966 + "A Familiar Stranger" tvdb-425354, extracted from these
    series' own embedded Chinese subtitle streams)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_mines_recurring_name_across_episodes(self):
        lines = ["长庚来了", "长庚说话", "看到长庚"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="zh")
        candidates = mine_series_entities(self.root, "zh")
        self.assertIn("长庚", [c.canonical for c in candidates])

    def test_stopword_function_words_excluded(self):
        # Real measured cross-series overlap: recurs in ANY Chinese
        # dialogue regardless of show, so can never be a name.
        lines = ["什么我们知道", "什么我们知道", "什么我们知道"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="zh")
        candidates = [c.canonical for c in mine_series_entities(self.root, "zh")]
        self.assertNotIn("什么", candidates)
        self.assertNotIn("我们", candidates)

    def test_real_three_character_term_absorbs_its_own_substrings(self):
        # Real motivating case: "先元剑" (a named sword) independently
        # clears the threshold as a whole term AND its 2-character
        # sub-fragments ("先元", "元剑") also independently clear it
        # (every occurrence of the sword's name also IS an occurrence of
        # each fragment) -- only the longest, most informative candidate
        # should survive for a human reviewer.
        lines = ["先元剑在手", "先元剑出鞘", "先元剑归位"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="zh")
        candidates = [c.canonical for c in mine_series_entities(self.root, "zh")]
        self.assertIn("先元剑", candidates)
        self.assertNotIn("先元", candidates)
        self.assertNotIn("元剑", candidates)

    def test_fragment_with_independent_occurrences_survives_collapse(self):
        # A sub-fragment that ALSO recurs independently, outside the
        # longer term, is real evidence of its own and must not be
        # silently absorbed just because it also happens to appear
        # inside the longer term sometimes.
        lines = ["先元剑现身", "先元来了", "先元又来了"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="zh")
        candidates = [c.canonical for c in mine_series_entities(self.root, "zh")]
        self.assertIn("先元", candidates)

    def test_single_character_too_short_to_qualify(self):
        lines = ["他 来 了", "他 来 了", "他 来 了"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="zh")
        candidates = [c.canonical for c in mine_series_entities(self.root, "zh")]
        self.assertNotIn("他", candidates)


class UnregisteredLanguageTests(unittest.TestCase):
    """ko/zh/th have no orthographic mining strategy yet (see module
    docstring) -- mine_series_entities() must degrade to [], never raise,
    same as its existing "no match of any kind" behavior."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_unregistered_language_returns_empty(self):
        # th, not ko/zh -- both are registered now (see KoreanMiningTests,
        # ChineseMiningTests).
        lines = ["สวัสดีครับ", "สวัสดีครับ", "สวัสดีครับ"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="th")
        self.assertEqual(mine_series_entities(self.root, "th"), [])


class AutoLanguageDetectionTests(unittest.TestCase):
    """mine_series_entities_auto()/detect_series_mining_language() --
    fixes a real bug (2026-09-29): worker.py used to always mine with
    the hardcoded module SOURCE_LANG ("tr") regardless of the series'
    actual language, so every non-Turkish series silently mined zero
    candidates. See CLAUDE.md's dated entry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_detects_japanese_series_from_files_on_disk(self):
        lines = ["カナコ です", "はい カナコ", "カナコ 待って"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ja")
        self.assertEqual(detect_series_mining_language(self.root), "ja")

    def test_auto_mining_dispatches_to_the_detected_language(self):
        lines = ["カナコ です", "はい カナコ", "カナコ 待って"]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="ja")
        candidates = mine_series_entities_auto(self.root)
        self.assertIn("カナコ", [c.canonical for c in candidates])

    def test_no_registered_language_files_returns_none(self):
        self.assertIsNone(detect_series_mining_language(self.root))
        self.assertEqual(mine_series_entities_auto(self.root), [])

    def test_turkish_series_still_auto_detected(self):
        # The pre-existing Turkish-only behavior must keep working
        # unchanged through the new auto-detecting entry point.
        lines = ["Eda geldi.", "Nerede Eda?", "Eda çok mutlu."]
        for i in range(1, 4):
            _write_pair(self.root, f"S01E0{i}", lines, lang="tr")
        self.assertEqual(detect_series_mining_language(self.root), "tr")
        candidates = mine_series_entities_auto(self.root)
        self.assertIn("Eda", [c.canonical for c in candidates])


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
