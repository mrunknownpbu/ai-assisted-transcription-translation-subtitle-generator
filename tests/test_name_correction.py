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
    """The series vocabulary comes from audio-derived cached transcripts,
    never from subtitle files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.series = Path(self.tmp.name) / "media" / "Show"
        self.cache = Path(self.tmp.name) / "cache"
        self.cache.mkdir()
        nc._series_vocab_cache.clear()

    def _cache(self, name, media, words, *, language="tr", created=1.0, suppressed=False):
        import json
        data = {"media_path": str(self.series / media), "media_hash": name, "audio_stream_index": 0,
                "language": language, "created_at": created,
                "segments": [{"suppressed": suppressed,
                              "words": [{"text": w} for w in words]}]}
        (self.cache / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def test_counts_lowercase_words_of_other_episodes_and_excludes_the_current_one(self):
        self._cache("a", "S01E01.mkv", ["Çilek", "yedim", "çilek"])
        self._cache("b", "S01E02.mkv", ["çilek"])
        vocab = nc.series_vocabulary(self.series, "tr", self.cache)
        self.assertEqual(vocab["cilek"], 2)   # lowercase only: "Çilek" is not counted
        only_other = nc.series_vocabulary(self.series, "tr", self.cache,
                                          exclude_media=self.series / "S01E02.mkv")
        self.assertEqual(only_other["cilek"], 1)

    def test_subtitle_files_are_never_read(self):
        (self.series).mkdir(parents=True)
        (self.series / "S01E09.tr.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nçilek çilek çilek\n\n", encoding="utf-8")
        self._cache("a", "S01E01.mkv", ["yedim"])
        self.assertEqual(nc.series_vocabulary(self.series, "tr", self.cache)["cilek"], 0)

    def test_other_series_languages_and_suppressed_segments_are_ignored(self):
        self._cache("a", "S01E01.mkv", ["çilek"], language="ms")
        self._cache("b", "S01E02.mkv", ["çilek"], suppressed=True)
        import json
        other = json.loads((self.cache / "a.json").read_text())
        other["media_path"] = str(Path(self.tmp.name) / "media" / "Other" / "S01E01.mkv")
        other["language"] = "tr"
        (self.cache / "c.json").write_text(json.dumps(other), encoding="utf-8")
        self.assertEqual(nc.series_vocabulary(self.series, "tr", self.cache)["cilek"], 0)

    def test_only_the_newest_cache_entry_per_episode_counts(self):
        self._cache("old", "S01E01.mkv", ["çilek"], created=1.0)
        self._cache("new", "S01E01.mkv", ["çilek", "çilek"], created=2.0)
        self.assertEqual(nc.series_vocabulary(self.series, "tr", self.cache)["cilek"], 2)

    def test_missing_cache_or_series_gives_an_empty_vocabulary(self):
        self.assertEqual(nc.series_vocabulary(None, "tr", self.cache), nc.Counter())
        self.assertEqual(nc.series_vocabulary(self.series, "tr", None), nc.Counter())
        self.assertEqual(nc.series_vocabulary(self.series, "tr", Path(self.tmp.name) / "nope"), nc.Counter())

    def test_new_cache_files_invalidate_the_memo(self):
        self._cache("a", "S01E01.mkv", ["çilek"])
        self.assertEqual(nc.series_vocabulary(self.series, "tr", self.cache)["cilek"], 1)
        self._cache("b", "S01E02.mkv", ["çilek"])
        self.assertEqual(nc.series_vocabulary(self.series, "tr", self.cache)["cilek"], 2)


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

    def test_subtitle_files_never_influence_the_transcript(self):
        """Audio is the only source for Workflow A: a series subtitle that
        writes "aydın" lowercase everywhere must neither veto the
        correction nor even be parsed."""
        series = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, series, ignore_errors=True)
        (series / "S01E09.tr.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n" + "aydın " * 20 + "\n\n", encoding="utf-8")
        with patch("srt.parse", side_effect=AssertionError("a subtitle file was parsed")):
            text, events = self.run_pipeline(
                {"names_here": {"Aydan"}, "known": {"Aydan"}, "series_root": series})
        self.assertIn("Aydan", text)
        self.assertEqual(events["NAME_CORRECTIONS_APPLIED"]["words"], 1)

    def test_no_context_changes_nothing(self):
        text, events = self.run_pipeline(None)
        self.assertIn("Aydın", text)
        self.assertNotIn("NAME_CORRECTIONS_APPLIED", events)


if __name__ == "__main__":
    unittest.main()
