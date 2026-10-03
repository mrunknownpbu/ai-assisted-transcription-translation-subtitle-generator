"""scripts/eval_transcription.py's scoring pieces (the ASR-running parts
are exercised on real episodes -- benchmark-results/)."""

import collections
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "eval_transcription", Path(__file__).resolve().parent.parent / "scripts" / "eval_transcription.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


class NormaliseTests(unittest.TestCase):
    def test_turkish_numbers(self):
        self.assertEqual(ev.turkish_number(10), ["on"])
        self.assertEqual(ev.turkish_number(25), ["yirmi", "beş"])
        self.assertEqual(ev.turkish_number(100), ["yüz"])
        self.assertEqual(ev.turkish_number(1998), ["bin", "dokuz", "yüz", "doksan", "sekiz"])
        self.assertEqual(ev.turkish_number(0), ["sıfır"])

    def test_numerals_and_words_compare_equal(self):
        self.assertEqual(ev.normalise("10"), ev.normalise("on"))
        self.assertEqual(ev.normalise("Saat 10'da geldim."), ev.normalise("Saat onda geldim."))
        self.assertEqual(ev.normalise("25"), ["yirmi", "beş"])

    def test_turkish_case_apostrophes_circumflex_and_sdh(self):
        self.assertEqual(ev.normalise("(Eda) SERKAN'IN İşi, herhâlde!"), ["serkanın", "işi", "herhalde"])

    def test_colloquial_variants_match(self):
        self.assertEqual(ev.normalise("Valla abicim baya"), ev.normalise("Vallahi abiciğim bayağı"))


class LanguageNormaliseTests(unittest.TestCase):
    """--lang selects the tokenisation: characters for unspaced scripts."""

    def setUp(self):
        self.addCleanup(setattr, ev, "LANGUAGE", ev.LANGUAGE)

    def test_japanese_is_per_character_and_ignores_kana_script_and_punctuation(self):
        ev.LANGUAGE = "ja"
        self.assertEqual(ev.normalise("（笑）今日は、いい天気ですね！"), list("今日はいい天気ですね"))
        self.assertEqual(ev.normalise("カナコ"), ev.normalise("かなこ"))

    def test_chinese_and_thai_are_per_character_with_tags_removed(self):
        ev.LANGUAGE = "zh"
        self.assertEqual(ev.normalise("{\\an8}你好，世界！"), list("你好世界"))
        ev.LANGUAGE = "th"
        self.assertEqual(ev.normalise("<i>สวัสดี</i> ครับ"), list("สวัสดีครับ"))

    def test_other_languages_are_per_word_without_turkish_rules(self):
        ev.LANGUAGE = "ms"
        self.assertEqual(ev.normalise("Saya nak pergi, KE sana!"), ["saya", "nak", "pergi", "ke", "sana"])
        self.assertEqual(ev.normalise("10 valla"), ["10", "valla"])


class PreparedAudioTests(unittest.TestCase):
    def test_finds_wav_named_by_video_path_hash(self):
        import hashlib
        video = Path("/media/Show/S01E01.mkv")
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / (hashlib.sha1(str(video).encode()).hexdigest()[:16] + ".wav")
            wav.write_bytes(b"RIFF")
            self.assertEqual(ev.prepared_audio(video, d), wav)

    def test_missing_wav_stops_the_run(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit):
            ev.prepared_audio(Path("/media/Show/S01E01.mkv"), d)


class AlignTests(unittest.TestCase):
    def test_counts_each_error_type(self):
        s, d, i, subs = ev.align(["serkan", "eve", "geldi"], ["sarkan", "geldi"])
        self.assertEqual((s, d, i), (1, 1, 0))
        self.assertEqual(subs, [("serkan", "sarkan")])
        self.assertEqual(ev.align(["eda", "geldi"], ["eda", "de", "geldi"])[:3], (0, 0, 1))

    def test_identical_is_zero(self):
        self.assertEqual(ev.align(["a", "b"], ["a", "b"])[:3], (0, 0, 0))


class ScoreTests(unittest.TestCase):
    def test_name_recall_and_suffixes(self):
        names = {"serkan": "Serkan", "eda": "Eda"}
        ref = {0: ev.normalise("Serkan'ın annesi Eda'yı aradı. Serkan!")}
        hyp = {0: ev.normalise("Sarkan'ın annesi Eda'yı aradı. Serkan!")}
        r = ev.score(ref, hyp, names)
        self.assertEqual(r["name_ref"], {"Serkan": 2, "Eda": 1})
        self.assertEqual(r["name_hit"], {"Serkan": 1, "Eda": 1})
        self.assertGreater(r["wer"], 0)

    def test_missing_minute_counts_as_missed(self):
        r = ev.score({0: ["a"] * 10, 1: ["b"] * 10}, {0: ["a"] * 10}, {})
        self.assertEqual((r["missed"], r["wer"]), (50.0, 50.0))


class ReferenceCheckTests(unittest.TestCase):
    def _db(self, rows):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        conn = sqlite3.connect(tmp.name)
        conn.execute("CREATE TABLE jobs (job_type TEXT, outputs TEXT, finished_at REAL)")
        conn.executemany("INSERT INTO jobs VALUES (?,?,?)", rows)
        conn.commit()
        return tmp.name

    def test_last_writer_decides(self):
        ref = Path("/data/Show/S01E01.tr.srt")
        video_then_human = self._db([("video", json.dumps([str(ref)]), 1.0),
                                     ("srt_translation", json.dumps([str(ref)]), 2.0)])
        human_then_video = self._db([("srt_translation", json.dumps([str(ref)]), 1.0),
                                     ("video", json.dumps([str(ref)]), 2.0)])
        self.assertFalse(ev.app_written(ref, video_then_human))
        self.assertTrue(ev.app_written(ref, human_then_video))
        self.assertFalse(ev.app_written(ref, self._db([])))


if __name__ == "__main__":
    unittest.main()


class LyricTests(unittest.TestCase):
    def test_quoted_cues_are_lyrics(self):
        self.assertTrue(ev.is_lyric('"Gün olur, ben de gelirim"'))
        self.assertTrue(ev.is_lyric('- “Bir yitik düş ülkesi bu”'))
        self.assertTrue(ev.is_lyric("♪ la la ♪"))
        self.assertFalse(ev.is_lyric("Selin, gel buraya."))


class SegmentationNaturalnessTests(unittest.TestCase):
    """--segmentation: our source cues vs. human cues of the same episode."""

    def test_match_points_greedy_nearest_within_tolerance(self):
        tp, ref_n, hyp_n = ev.match_points([1.0, 5.0, 9.0], [1.3, 5.5, 20.0], tol=0.4)
        self.assertEqual((tp, ref_n, hyp_n), (1, 3, 3))  # only 1.0<->1.3 (0.3s) is within tolerance
        self.assertEqual(ev.match_points([1.0], [1.0]), (1, 1, 1))
        self.assertEqual(ev.match_points([], [1.0]), (0, 0, 1))

    def test_prf(self):
        self.assertEqual(ev.prf(2, 4, 2), (1.0, 0.5, 2 / 3))
        self.assertEqual(ev.prf(0, 0, 0), (0.0, 0.0, 0.0))

    def test_is_interjection_does_not_catch_ordinary_words(self):
        self.assertTrue(ev.is_interjection("aa"))
        self.assertFalse(ev.is_interjection("tamam"))  # "okay" -- an ordinary word, not an interjection
        self.assertFalse(ev.is_interjection("aydan"))

    def _lines(self, *rows):
        from srt import SrtCueLines
        return [SrtCueLines(start=t, end=t + 1.5, lines=list(ls)) for t, ls in rows]

    def test_dash_turn_points_only_two_speaker_dialogue_cues(self):
        pts = self.test_lines = self._lines(
            (0.0, ["- Tamam.", "- Valizin hazır mı?"]),   # a real two-speaker cue -> one turn point
            (10.0, ["Tek satır bir cümle."]),              # not dialogue-dash shaped
            (20.0, ["- Sadece bir konuşmacı devam ediyor"]),  # one dash line only, not a turn
        )
        out = ev.dash_turn_points(pts)
        self.assertEqual(out, [0.75])

    def test_segmentation_stats_boundary_and_turn_recall(self):
        from transcript import Segment, Word
        ref = self._lines((0.0, ["- Tamam.", "- Valizin hazır mı?"]), (5.0, ["Devam ediyor."]))

        def seg(i, start, end, text):
            return Segment(index=i, start=start, end=end,
                           words=[Word(text=text, original_text=text, start=start, end=end)],
                           avg_logprob=0.0, no_speech_prob=0.0, compression_ratio=0.0)

        # A system that never splits inside the dash cue (no diarization) --
        # its only boundary is near the ref's cue-to-cue gap, not the turn.
        sys_cues = [seg(0, 0.0, 1.5, "Tamam. Valizin hazır mı?"), seg(1, 5.0, 6.5, "Devam ediyor.")]
        stats = ev.segmentation_stats(sys_cues, ref)
        out = ev.format_segmentation(stats)
        self.assertEqual(out["turn_recall"], 0.0)   # the turn INSIDE the first cue is never recovered
        self.assertEqual(out["turn_points"], 1)
        self.assertGreater(out["boundary_f1"], 0.0)  # the cue-to-cue boundary at ~3.25s is still found

    def test_format_segmentation_handles_no_turn_points(self):
        out = ev.format_segmentation(collections.Counter(
            bound_tp=0, bound_ref=0, bound_hyp=0, turn_hit=0, turn_n=0,
            ref_over=0, ref_n_cues=1, ref_mid=0, sys_over=0, sys_n_cues=1, sys_mid=0,
            ref_dur=1, sys_dur=1, interj_ref=0, interj_hit=0))
        self.assertIsNone(out["turn_recall"])
        self.assertIsNone(out["interjection_recall"])
