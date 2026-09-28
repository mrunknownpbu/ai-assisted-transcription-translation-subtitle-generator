"""name_correction.py: misheard character names, fixed from the episode's
cast. Real cases: Aydan -> "Aydın" x33 and Selin -> "Selim" x11 across 7
Love Is In The Air episodes; the guards exist because "Senin" (your),
"Çilek" (strawberry), "Gelin" (come) were wrongly "corrected" without them."""

import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import yaml

import name_correction as nc
from transcript import Word


def w(text):
    return Word(text=text, original_text=text, start=0.0, end=0.5)


HERE = {"Aydan", "Selin", "Serkan", "Bolat", "Melo"}
KNOWN = HERE | {"Ayfer", "Eda"}


class OneEditTests(unittest.TestCase):
    def test_distance_exactly_one(self):
        for a, b in (("aydin", "aydan"), ("selim", "selin"), ("bulat", "bolat"), ("haydan", "aydan")):
            self.assertTrue(nc._one_edit_apart(a, b), (a, b))
        for a, b in (("aydan", "aydan"), ("melih", "melo"), ("ab", "abcd")):
            self.assertFalse(nc._one_edit_apart(a, b), (a, b))


class CorrectWordTests(unittest.TestCase):
    def fix(self, text, here=HERE, known=KNOWN, vocab=None):
        word = nc.correct_word(w(text), here, known, vocab)
        return word.text, word

    def test_corrects_and_keeps_suffix_and_punctuation(self):
        text, word = self.fix("Aydın'ın,")
        self.assertEqual(text, "Aydan'ın,")
        self.assertEqual(word.original_text, "Aydın'ın,")
        self.assertEqual(word.corrections[0].rule_id, "cast-name-one-edit")

    def test_leaves_lowercase_words_real_names_and_ambiguity_alone(self):
        self.assertEqual(self.fix("aydın")[0], "aydın")                       # the word
        self.assertEqual(self.fix("Selim", known=KNOWN | {"Selim"})[0], "Selim")  # a real character
        self.assertEqual(self.fix("Serkan")[0], "Serkan")                     # already right
        self.assertEqual(self.fix("Bolan", here={"Bolat", "Bolan2", "Bolak"})[0], "Bolan")  # two candidates
        self.assertEqual(self.fix("Ada")[0], "Ada")                           # too short

    def test_ordinary_word_capitalised_at_sentence_start_is_left_alone(self):
        vocab = nc.Vocabulary([w("Çilek")], Counter({"cilek": 5}))
        self.assertEqual(self.fix("Çilek", here={"Çiçek"}, known={"Çiçek"}, vocab=vocab)[0], "Çilek")
        vocab = nc.Vocabulary([w("Senin")], Counter({"senin": 927}))
        self.assertEqual(self.fix("Senin", vocab=vocab)[0], "Senin")

    def test_name_capitalised_more_often_than_lowercased_is_corrected(self):
        # "aydın" appears lowercase 3x in the series, "Aydın" 4x in this episode.
        vocab = nc.Vocabulary([w("Aydın")] * 4, Counter({"aydin": 3}))
        self.assertEqual(self.fix("Aydın", vocab=vocab)[0], "Aydan")

    def test_correct_words_builds_the_vocabulary_from_the_episode(self):
        words = [w("Gelin"), w("gelin"), w("Selim")]
        out = [x.text for x in nc.correct_words(words, {"Selin"}, {"Selin"})]
        self.assertEqual(out, ["Gelin", "gelin", "Selin"])

    def test_no_names_is_a_no_op(self):
        self.assertEqual(nc.correct_words([w("Aydın")], set(), set())[0].text, "Aydın")

    def test_off_switch(self):
        with patch.dict("os.environ", {"SUBTITLE_AI_NAME_CORRECTION": "off"}):
            self.assertFalse(nc.enabled())
        with patch.dict("os.environ", {"SUBTITLE_AI_NAME_CORRECTION": ""}):
            self.assertTrue(nc.enabled())


class EpisodeNamesTests(unittest.TestCase):
    def test_glossary_and_cast_report_scoped_to_the_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "s.yaml").write_text(yaml.safe_dump({"tvdb_id": 1, "entities": [
                {"canonical": "Eda", "aliases": ["Eda Yıldız"], "protected": True},
                {"canonical": "Deniz", "protected": True, "episodes": ["S01E29-E39"]}]}), encoding="utf-8")
            report = {"candidates": [
                {"name": "Aydan", "nicknames": [], "full_names": ["Aydan Bolat"], "scope": None},
                {"name": "Balca", "nicknames": [], "full_names": [], "scope": ["S01E22-E31"]},
                {"name": "Melek", "nicknames": ["Melo"], "full_names": [], "scope": None}]}
            here, known = nc.episode_names(1, (1, 5), tmp, report)
            self.assertEqual(here, {"Yıldız", "Aydan", "Bolat", "Melek", "Melo"})  # "Eda" < 4 letters
            self.assertIn("Balca", known)
            self.assertIn("Deniz", known)
            self.assertNotIn("Balca", here)
            here30, _ = nc.episode_names(1, (1, 30), tmp, report)
            self.assertTrue({"Deniz", "Balca"} <= here30)


class SeriesVocabularyTests(unittest.TestCase):
    def test_counts_lowercase_words_and_excludes_the_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = "1\n00:00:01,000 --> 00:00:02,000\n{}\n\n"
            Path(tmp, "S01E01.tr.srt").write_text(cue.format("Çilek yedim, çilek güzel."), encoding="utf-8")
            Path(tmp, "S01E02.tr.srt").write_text(cue.format("çilek"), encoding="utf-8")
            nc._series_vocab_cache.clear()
            self.assertEqual(nc.series_vocabulary(Path(tmp), "tr")["cilek"], 2)
            self.assertEqual(nc.series_vocabulary(Path(tmp), "tr", exclude=Path(tmp, "S01E02.tr.srt"))["cilek"], 1)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
class PipelineIntegrationTests(unittest.TestCase):
    """pipeline.run() applies the correction before segmentation, so the
    translation step sees the corrected name, and reports it as an event."""

    def run_pipeline(self, context):
        import media
        import pipeline
        from asr import PIPELINE_VERSION
        from transcript import CanonicalTranscript, ModelInfo, Segment
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        video = Path(tmp.name) / "S01E01.mkv"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=1",
                        "-c:a", "pcm_s16le", str(video)], check=True, capture_output=True)
        words = [Word(text=t, original_text=t, start=0.1 * i, end=0.1 * i + 0.1, probability=0.9)
                 for i, t in enumerate(["Aydın", "geldi."])]
        seg = Segment(index=0, start=0.0, end=0.3, words=words, avg_logprob=-0.1,
                      no_speech_prob=0.0, compression_ratio=1.0)
        transcript = CanonicalTranscript(
            media_path=str(video), media_hash=media.content_fingerprint(video), audio_stream_index=0,
            language="tr", language_probability=0.99, asr_model=ModelInfo(name="x", version="large-v3"),
            alignment_model=None, pipeline_version=PIPELINE_VERSION, segments=[seg])
        seen, events = [], []
        fake_translate = lambda cues, spans, src, **kw: seen.extend(c.text for c in cues) or ["x"] * len(spans)
        with patch.object(pipeline, "asr_transcribe", return_value=transcript), \
             patch.object(pipeline.translate, "translate_spans", side_effect=fake_translate):
            pipeline.run(video_path=str(video), media_root=tmp.name, work_dir=str(Path(tmp.name) / "w"),
                         write_output=False, stream_sampler=lambda wav: ("tr", 0.9),
                         name_correction_context=context, on_event=lambda n, d: events.append((n, d)))
        return " ".join(seen), dict(events)

    def test_corrected_name_reaches_translation(self):
        text, events = self.run_pipeline({"names_here": {"Aydan"}, "known": {"Aydan"}, "series_root": None})
        self.assertIn("Aydan", text)
        self.assertEqual(events["NAME_CORRECTIONS_APPLIED"]["words"], 1)

    def test_no_context_changes_nothing(self):
        text, events = self.run_pipeline(None)
        self.assertIn("Aydın", text)
        self.assertNotIn("NAME_CORRECTIONS_APPLIED", events)


if __name__ == "__main__":
    unittest.main()
