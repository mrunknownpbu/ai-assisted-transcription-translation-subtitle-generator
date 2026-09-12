import inspect
import unittest

import glossary as glossary_mod
from glossary import (Entity, build_glossary, entity_occurrence_report, protect,
                      recover_dropped_entities, restore)


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


if __name__ == "__main__":
    unittest.main()
