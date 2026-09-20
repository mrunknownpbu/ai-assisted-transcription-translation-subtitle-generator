import inspect
import unittest

import glossary as glossary_mod
from glossary import (Entity, PhraseEntry, bare_entity_translation,
                      build_glossary, build_phrase_map,
                      entity_occurrence_report, join_multi_speaker_dash_lines,
                      protect, recover_dropped_entities,
                      repair_corrupted_placeholders, restore,
                      split_into_sentences, split_multi_speaker_dash_lines)


class ProtectRestoreTests(unittest.TestCase):
    def test_protect_then_restore_round_trips(self):
        g = build_glossary([Entity("Eda Yıldız", ["Eda Yıldız", "Eda"])])
        protected = protect("Eda bugün burada.", g)
        self.assertNotIn("Eda", protected)
        restored = restore("Eda is here today.".replace("Eda", protected.split()[0]), g)
        self.assertIn("Eda Yıldız", restored)

    def test_multi_word_entity_protected_as_one_unit(self):
        g = build_glossary([Entity("Eda Yıldız", ["Eda Yıldız", "Eda"])])
        protected = protect("Eda Yıldız geldi.", g)
        self.assertNotIn("Yıldız", protected)


class EntityOccurrenceTests(unittest.TestCase):
    def test_matching_counts_no_mismatch(self):
        g = build_glossary([Entity("Eda", ["Eda"])])
        source = protect("Eda geldi. Eda gitti.", g)
        report = entity_occurrence_report(source, "Eda arrived. Eda left.", g)
        self.assertEqual(report["Eda"], (2, 2))

    def test_dropped_mention_detected(self):
        g = build_glossary([Entity("Eda", ["Eda"])])
        source = protect("Eda geldi. Eda gitti.", g)
        report = entity_occurrence_report(source, "Someone arrived.", g)
        self.assertEqual(report["Eda"], (2, 0))


class RecoverDroppedEntitiesTests(unittest.TestCase):
    def test_never_takes_a_model_parameter(self):
        # Structural guarantee, not convention: this function cannot call
        # NLLB because it has nothing to call it with.
        params = inspect.signature(recover_dropped_entities).parameters
        self.assertNotIn("model", params)
        self.assertNotIn("tok", params)
        self.assertNotIn("device", params)

    def test_tops_up_missing_mention_deterministically(self):
        g = build_glossary([Entity("Eda", ["Eda"])])
        source = protect("Eda! Eda! Kızım hadi uyan!", g)
        result = recover_dropped_entities(source, "My daughter, wake up!", g)
        self.assertEqual(result.count("Eda"), 2)

    def test_does_not_touch_a_candidate_that_already_has_enough(self):
        g = build_glossary([Entity("Eda", ["Eda"])])
        source = protect("Eda geldi.", g)
        result = recover_dropped_entities(source, "Eda arrived.", g)
        self.assertEqual(result, "Eda arrived.")

    def test_never_invents_semantic_content_beyond_the_entity_itself(self):
        g = build_glossary([Entity("Eda", ["Eda"])])
        source = protect("Eda!", g)
        result = recover_dropped_entities(source, "", g)
        # Only the canonical name (+ punctuation), nothing else.
        self.assertEqual(result.strip(), "Eda!")


class CjkBoundaryTests(unittest.TestCase):
    """Real defect (Japanese validation, 2026-09-13): Python's \\b is
    Unicode-\\w-based, so it never matches around a name embedded directly
    in continuous Japanese/Chinese text with no surrounding whitespace --
    the normal way those scripts are written. glossary.py's internal
    _bounded() helper redefines the boundary in ASCII-alnum terms instead
    (see its docstring); these tests protect that fix directly."""

    def test_protects_entity_embedded_in_continuous_japanese_with_no_spaces(self):
        g = build_glossary([Entity("アリス", ["アリス"])])
        protected = protect("アリスは走った", g)
        self.assertNotIn("アリス", protected)

    def test_restores_placeholder_spliced_into_continuous_japanese(self):
        g = build_glossary([Entity("アリス", ["アリス"])])
        protected = protect("アリスは走った", g)
        # Simulate the placeholder surviving translation untouched, still
        # glued directly to non-Latin text with no separating space.
        translated = protected + "です"
        restored = restore(translated, g)
        self.assertIn("アリス", restored)

    def test_occurrence_count_finds_canonical_embedded_in_japanese(self):
        g = build_glossary([Entity("アリス", ["アリス"])])
        self.assertEqual(glossary_mod.occurrence_count("アリスとカルベ", "アリス"), 1)

    def test_latin_script_word_boundary_behavior_is_unchanged(self):
        # No regression: "Cenk" must still not match inside "Cenkiz".
        g = build_glossary([Entity("Cenk", ["Cenk"])])
        protected = protect("Cenkiz geldi.", g)
        self.assertIn("Cenk", protected)
        self.assertNotIn(list(g.values())[0][0], protected)


class TurkishCapitalDottedITests(unittest.TestCase):
    """Real defect (2026-09-19): str.casefold() maps Turkish capital
    dotted "İ" (U+0130) to a two-codepoint sequence ("i" + combining dot
    above, U+0307), a different string than the real "İ". build_glossary()
    used to key its dict by the casefolded form and protect() regex-
    matched directly against that key, so an entity like "İstanbul"
    silently never matched real "İstanbul" text at all."""

    def test_entity_with_capital_dotted_i_is_protected(self):
        g = build_glossary([Entity("İstanbul", ["İstanbul"])])
        protected = protect("İstanbul'da yaşıyorum.", g)
        self.assertNotIn("İstanbul", protected)

    def test_lowercase_and_uppercase_variants_still_match_case_insensitively(self):
        g = build_glossary([Entity("İstanbul", ["İstanbul"])])
        protected = protect("istanbul'a gittim. İSTANBUL çok güzel.", g)
        self.assertNotIn("istanbul", protected.casefold())

    def test_restore_still_reinserts_the_canonical_spelling(self):
        g = build_glossary([Entity("İstanbul", ["İstanbul"])])
        protected = protect("İstanbul'da yaşıyorum.", g)
        self.assertIn("İstanbul", restore(protected, g))


class BuildPhraseMapTests(unittest.TestCase):
    """PhraseEntry/build_phrase_map (added 2026-09-19): a forced whole-
    segment translation, distinct from Entity's name-protection -- see
    real QC evidence in glossary.PhraseEntry's docstring."""

    def test_language_matched_phrase_is_included(self):
        phrases = [PhraseEntry(source="Peki.", translation="Okay.", language="tr")]
        m = build_phrase_map(phrases, "tr")
        self.assertEqual(m["peki"], "Okay.")

    def test_language_mismatched_phrase_is_excluded(self):
        phrases = [PhraseEntry(source="Peki.", translation="Okay.", language="tr")]
        m = build_phrase_map(phrases, "es")
        self.assertEqual(m, {})

    def test_universal_language_none_applies_regardless_of_detected_language(self):
        phrases = [PhraseEntry(source="OK", translation="OK", language=None)]
        self.assertEqual(build_phrase_map(phrases, "tr")["ok"], "OK")
        self.assertEqual(build_phrase_map(phrases, "es")["ok"], "OK")

    def test_phrase_key_normalizes_trailing_punctuation_and_case(self):
        phrases = [PhraseEntry(source="Peki.", translation="Okay.", language="tr")]
        m = build_phrase_map(phrases, "tr")
        self.assertIn("peki", m)
        self.assertNotIn("peki.", m)


class BareEntityTranslationTests(unittest.TestCase):
    """Real evidence (S01E02 QC run, 2026-09-20): NLLB hallucinated
    "Cenk, what's going on?" from bare "Cenk." despite Cenk already being
    a protected entity -- protection alone doesn't stop the model padding
    out a placeholder-only input. bare_entity_translation() lets the
    caller detect that shape and skip the model entirely."""

    def test_bare_single_mention_returns_restored_text(self):
        g = build_glossary([Entity("Cenk", ["Cenk"])])
        protected = protect("Cenk.", g)
        self.assertEqual(bare_entity_translation(protected, g), "Cenk.")

    def test_bare_mention_with_exclamation_returns_restored_text(self):
        g = build_glossary([Entity("Sirius", ["Sirius"])])
        protected = protect("Sirius!", g)
        self.assertEqual(bare_entity_translation(protected, g), "Sirius!")

    def test_two_bare_mentions_together_are_still_bare(self):
        g = build_glossary([Entity("Cenk", ["Cenk"]), Entity("Sirius", ["Sirius"])])
        protected = protect("Cenk, Sirius!", g)
        self.assertEqual(bare_entity_translation(protected, g), "Cenk, Sirius!")

    def test_real_sentence_containing_a_protected_name_is_not_bare(self):
        g = build_glossary([Entity("Cenk", ["Cenk"])])
        protected = protect("Cenk'in haberi var mı?", g)
        self.assertIsNone(bare_entity_translation(protected, g))

    def test_no_placeholder_present_returns_none(self):
        g = build_glossary([Entity("Cenk", ["Cenk"])])
        protected = protect("Merhaba.", g)
        self.assertIsNone(bare_entity_translation(protected, g))

    def test_empty_glossary_returns_none(self):
        self.assertIsNone(bare_entity_translation("Cenk.", {}))

    def test_phrase_key_matches_variant_trailing_punctuation(self):
        # "Peki." / "Peki!" / "Peki?" -- real subtitle punctuation varies
        # on short interjections; all must hit the same glossary entry.
        phrases = [PhraseEntry(source="Peki.", translation="Okay.", language="tr")]
        m = build_phrase_map(phrases, "tr")
        self.assertEqual(m[glossary_mod._phrase_key("Peki!")], "Okay.")
        self.assertEqual(m[glossary_mod._phrase_key("peki?")], "Okay.")


class RepairCorruptedPlaceholdersTests(unittest.TestCase):
    """Real evidence (S01E05 QC run, 2026-09-20): Serkan's real placeholder
    "Xac" came back from NLLB as "Xax" (a one-character substitution) and
    restore() left the raw corrupted token in the final subtitle since it
    only does exact matching. Scoped to the placeholders active in THIS
    sentence's own protected source, not the whole job's glossary -- see
    the function's docstring for why a whole-glossary scope collides too
    often on a show with a double-digit cast."""

    def test_one_character_corruption_is_repaired_then_restored(self):
        g = build_glossary([Entity("Serkan", ["Serkan"])])
        source_protected = protect("Serkan'ı nasıl kıskandığını.", g)  # "Xaa'ı ..."
        corrupted = "- How jealous she was of Xab."
        repaired = repair_corrupted_placeholders(corrupted, source_protected)
        self.assertIn("Xaa", repaired)
        self.assertEqual(restore(repaired, g), "- How jealous she was of Serkan.")

    def test_exact_placeholder_is_left_alone(self):
        g = build_glossary([Entity("Serkan", ["Serkan"])])
        source_protected = protect("Serkan'ı nasıl kıskandığını.", g)
        text = "- How jealous she was of Xaa."
        self.assertEqual(repair_corrupted_placeholders(text, source_protected), text)

    def test_unrelated_shape_is_left_alone(self):
        g = build_glossary([Entity("Serkan", ["Serkan"])])
        source_protected = protect("Serkan'ı nasıl kıskandığını.", g)
        text = "He works at Xerox."
        self.assertEqual(repair_corrupted_placeholders(text, source_protected), text)

    def test_two_active_placeholders_equidistant_is_left_alone(self):
        # Real case: two names mentioned in the SAME sentence (Serkan ->
        # Xac, Ferit -> Xak in production) can both be exactly one edit
        # from the same corrupted token -- must not guess either way.
        g = build_glossary([Entity("Alpha", ["Alpha"]), Entity("Beta", ["Beta"])])
        placeholders = sorted({p for p, _c in g.values()})  # ["Xaa", "Xab"]
        source_protected = f"{placeholders[0]} and {placeholders[1]} talked."
        text = "Xac said hello."  # one edit from both Xaa and Xab
        self.assertEqual(repair_corrupted_placeholders(text, source_protected), text)

    def test_no_placeholders_active_in_source_returns_text_unchanged(self):
        self.assertEqual(
            repair_corrupted_placeholders("Xab said hello.", "Hello, how are you?"),
            "Xab said hello.",
        )


class MultiSpeakerDashLinesTests(unittest.TestCase):
    """Real evidence (S01E01 QC, 2026-09-20): a two-speaker dash-cue sent
    to NLLB as one string comes back garbled or truncated. Both real
    examples below are the actual source text that produced actual real
    broken output ("Serkan. - Good morning to you, Mr. Serkan." and
    "Evren? - Mr." respectively)."""

    def test_identical_dash_lines_split(self):
        text = "- Günaydın Serkan Bey.\n- Günaydın Serkan Bey."
        self.assertEqual(split_multi_speaker_dash_lines(text),
                         ["Günaydın Serkan Bey.", "Günaydın Serkan Bey."])

    def test_different_dash_lines_split_no_space_after_dash(self):
        text = "-Evren Bey oradaki adam.\n-Hangisi?"
        self.assertEqual(split_multi_speaker_dash_lines(text),
                         ["Evren Bey oradaki adam.", "Hangisi?"])

    def test_single_line_returns_none(self):
        self.assertIsNone(split_multi_speaker_dash_lines("Merhaba nasılsın"))

    def test_single_dash_prefixed_line_returns_none(self):
        # Only one speaker turn -- nothing to split, not this shape.
        self.assertIsNone(split_multi_speaker_dash_lines("- Merhaba."))

    def test_mixed_dash_and_non_dash_lines_returns_none(self):
        # Ambiguous shape (only some lines are speaker turns) -- must not
        # guess, leave it as an ordinary sentence.
        self.assertIsNone(split_multi_speaker_dash_lines("- Merhaba.\nNasılsın?"))

    def test_join_reconstructs_dash_format(self):
        self.assertEqual(join_multi_speaker_dash_lines(["Hello.", "Hi there."]),
                         "- Hello.\n- Hi there.")


class SplitIntoSentencesTests(unittest.TestCase):
    """Real evidence (Season 01 full-batch QC, 2026-09-20): a two-sentence
    source span sent to NLLB in one generate() call silently lost the
    second sentence. Both real examples below are actual source text that
    produced actual real truncated output."""

    def test_real_two_sentence_span_splits(self):
        text = "Senin için çok seviniyorum. İtalya sana çok iyi gelecek."
        self.assertEqual(split_into_sentences(text),
                         ["Senin için çok seviniyorum.", "İtalya sana çok iyi gelecek."])

    def test_real_two_question_span_splits(self):
        text = "Sen çocuk mu kandırıyorsun? Sen benimle dalga mı geçiyorsun?"
        self.assertEqual(split_into_sentences(text),
                         ["Sen çocuk mu kandırıyorsun?", "Sen benimle dalga mı geçiyorsun?"])

    def test_single_sentence_returns_none(self):
        self.assertIsNone(split_into_sentences("Merhaba nasılsın."))

    def test_short_first_sentence_still_splits(self):
        # Real evidence (E09, second "Hişt!" occurrence, 2026-09-20): an
        # earlier version of this function required 2+ words before a
        # split point and left "Hişt! Çok eminim." merged, which both
        # missed the phrase-glossary's "Hişt." override AND still lost
        # "Hişt!" to NLLB outright -- a single-word first sentence must
        # split just like any other.
        self.assertEqual(split_into_sentences("Hişt! Çok eminim."),
                         ["Hişt!", "Çok eminim."])
        self.assertEqual(split_into_sentences("Oldu. Gidelim mi?"),
                         ["Oldu.", "Gidelim mi?"])

    def test_three_sentence_span_splits_all(self):
        text = "Eve gidiyorum. Çok yorgunum. Yarın görüşürüz."
        self.assertEqual(split_into_sentences(text),
                         ["Eve gidiyorum.", "Çok yorgunum.", "Yarın görüşürüz."])


if __name__ == "__main__":
    unittest.main()
