"""scripts/eval_transcription.py's scoring pieces (the ASR-running parts
are exercised on real episodes -- benchmark-results/)."""

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
