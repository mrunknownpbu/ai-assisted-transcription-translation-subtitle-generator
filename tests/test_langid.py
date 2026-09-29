"""langid.py -- text-based language ID used both by srt_translation.py's
source_lang="auto" and translate.py's per-sentence code-switch detection.

The three real false-positive/true-positive shapes below are pinned as
regression tests exactly as measured against the real Turkish media
library (2026-09-29, 90,076 cues / 43 episodes -- see CLAUDE.md and
langid.py's module docstring): a repeated Turkish line, an isolated
high-confidence misfire on a normal-length Turkish sentence, and the
real Spanish cold-open scene that motivated this module."""

import unittest

from langid import detect_language_overrides, detect_text_language

NLLB_LIKE_LANGS = frozenset({
    "tr", "en", "es", "fr", "de", "ru", "ar", "ja", "ko", "hi", "it", "pt",
    "nl", "vi", "th", "id", "he", "fa", "bn", "ur", "fi", "da", "no", "cs",
    "el", "ro", "ms", "tl", "so", "et",
})


class DetectTextLanguageTests(unittest.TestCase):
    def test_undetectable_text_returns_und(self):
        self.assertEqual(detect_text_language(""), ("und", 0.0))
        self.assertEqual(detect_text_language("   "), ("und", 0.0))

    def test_real_spanish_sentence_is_confident(self):
        lang, prob = detect_text_language("Este hombre es increíble.")
        self.assertEqual(lang, "es")
        self.assertGreater(prob, 0.99)

    def test_real_turkish_sentence_is_confident(self):
        lang, prob = detect_text_language("Hadi gel, sana kahve ısmarlayayım.")
        self.assertEqual(lang, "tr")
        self.assertGreater(prob, 0.99)


class DetectLanguageOverridesTests(unittest.TestCase):
    """supported_langs mirrors translate.NLLB_LANG's key set without
    importing translate.py -- langid.py must stay decoupled from it."""

    def test_no_code_switch_returns_all_none(self):
        texts = ["Hadi gel, sana kahve ısmarlayayım.", "Ateş Bey, sizi tekrar görmek ne kadar güzel."]
        overrides = detect_language_overrides(texts, "tr", supported_langs=NLLB_LIKE_LANGS)
        self.assertEqual(overrides, [None, None])

    def test_real_spanish_cold_open_scene_is_detected(self):
        # Real motivating case ("If You Love" S01E01, 2026-09-29): a
        # short cold-open scene entirely in Spanish, transcribed
        # correctly by Whisper but garbled/untranslated because the
        # whole job's translation was locked to Turkish. "Si, si." (7
        # chars) and "No es posible." (14 chars) are both deliberately
        # too short to qualify on their own (min_chars=20) and must stay
        # None -- same measured shape as the real library scan, which
        # found exactly this 3-member run (not 5) in this exact scene.
        texts = ["Si, si.", "Este hombre es increíble.", "Mire el estado de esta casa.",
                "Todas las noches es así.", "No es posible.", "Hanımefendi, ben söylediğinizden hiçbir şey..."]
        overrides = detect_language_overrides(texts, "tr", supported_langs=NLLB_LIKE_LANGS)
        self.assertEqual(overrides, [None, "es", "es", "es", None, None])

    def test_isolated_high_confidence_misfire_is_not_enough_alone(self):
        # Real measured case: "Lan sen kime vuruyor musun lan?" (31
        # chars, genuinely Turkish) detects as Finnish at p=1.0. A run of
        # exactly ONE such sentence, surrounded by ordinary Turkish
        # dialogue, must never override -- min_run=3 is what protects
        # this, not confidence or length alone.
        texts = ["Hadi gel, sana kahve ısmarlayayım.", "Lan sen kime vuruyor musun lan?",
                "Ateş Bey, sizi tekrar görmek ne kadar güzel."]
        overrides = detect_language_overrides(texts, "tr", supported_langs=NLLB_LIKE_LANGS)
        self.assertEqual(overrides, [None, None, None])

    def test_repeated_line_never_forms_a_run(self):
        # Real measured case: "Berit, Berit nerede?" (genuinely Turkish)
        # detects as German at p=0.999, and repeating it doesn't help --
        # the SAME wrong answer twice is still only a run of 2, and the
        # third occurrence here is a different (also-misdetected) line,
        # which must not silently extend the run either.
        texts = ["Berit, Berit nerede?", "Berit, Berit nerede?", "Evet, evet, tamam öyle olsun kardeşim."]
        overrides = detect_language_overrides(texts, "tr", supported_langs=NLLB_LIKE_LANGS)
        self.assertEqual(overrides, [None, None, None])

    def test_short_cue_neither_extends_nor_breaks_a_run(self):
        texts = ["Este hombre es increíble.", "Si.", "Mire el estado de esta casa.",
                "Todas las noches es así."]
        overrides = detect_language_overrides(texts, "tr", supported_langs=NLLB_LIKE_LANGS)
        self.assertEqual(overrides, ["es", None, "es", "es"])

    def test_run_shorter_than_minimum_does_not_override(self):
        texts = ["Este hombre es increíble.", "Mire el estado de esta casa.",
                "Hadi gel, sana kahve ısmarlayayım."]
        overrides = detect_language_overrides(texts, "tr", supported_langs=NLLB_LIKE_LANGS)
        self.assertEqual(overrides, [None, None, None])

    def test_unsupported_detected_language_never_overrides(self):
        texts = ["Este hombre es increíble.", "Mire el estado de esta casa.",
                "Todas las noches es así."]
        overrides = detect_language_overrides(texts, "tr", supported_langs=frozenset({"tr", "en"}))
        self.assertEqual(overrides, [None, None, None])

    def test_detected_language_matching_source_never_overrides(self):
        texts = ["Hadi gel, sana kahve ısmarlayayım.", "Ateş Bey, sizi tekrar görmek ne kadar güzel.",
                "Beni şikayet ediyor, çok kötü bir adam bu."]
        overrides = detect_language_overrides(texts, "tr", supported_langs=NLLB_LIKE_LANGS)
        self.assertEqual(overrides, [None, None, None])

    def test_empty_input(self):
        self.assertEqual(detect_language_overrides([], "tr", supported_langs=NLLB_LIKE_LANGS), [])

    def test_comma_merged_scene_is_still_detected(self):
        # Real gap found on a SECOND production verification pass
        # (2026-09-29): a fresh ASR decode of the exact same "If You
        # Love" S01E01 scene punctuated the Spanish dialogue as only 2
        # comma-joined sentences instead of the original run's 4-5
        # period-separated ones, so sentence-level detection alone never
        # reached min_run=3. Clause-level splitting recovers it: 3
        # qualifying comma/period fragments from those same 2 sentences,
        # verified to add zero new false positives across the whole
        # library scan (see langid.py's module docstring). The whole
        # flat sentence must still be marked as ONE unit each -- clause
        # splitting is a detection signal only, never a translation
        # split.
        texts = ["Ateş, Ateş, si, si.", "Este hombre es increible, mide el estado de esta casa.",
                "Todas las noches es asi, no es posible."]
        overrides = detect_language_overrides(texts, "tr", supported_langs=NLLB_LIKE_LANGS)
        self.assertEqual(overrides, [None, "es", "es"])


if __name__ == "__main__":
    unittest.main()
