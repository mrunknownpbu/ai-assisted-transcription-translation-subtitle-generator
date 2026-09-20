import tempfile
import unittest
import unittest.mock
from pathlib import Path

from srt_translation import SrtValidationError, parse_and_validate


def _write(tmp: Path, content: str, *, encoding: str = "utf-8") -> Path:
    path = tmp / "input.srt"
    path.write_bytes(content.encode(encoding))
    return path


VALID_SRT = (
    "1\n00:00:01,000 --> 00:00:03,500\nMerhaba dunya.\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nNasilsin?\n"
)


class ParseAndValidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_valid_srt_parses_all_cues(self):
        path = _write(self.dir, VALID_SRT)
        cues = parse_and_validate(path)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].number, 1)
        self.assertAlmostEqual(cues[0].start, 1.0)
        self.assertAlmostEqual(cues[0].end, 3.5)
        self.assertEqual(cues[0].text, "Merhaba dunya.")
        self.assertEqual(cues[1].text, "Nasilsin?")

    def test_windows_crlf_line_endings_are_handled(self):
        crlf = VALID_SRT.replace("\n", "\r\n")
        path = _write(self.dir, crlf)
        cues = parse_and_validate(path)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].text, "Merhaba dunya.")

    def test_utf8_bom_is_handled(self):
        path = self.dir / "bom.srt"
        path.write_bytes(b"\xef\xbb\xbf" + VALID_SRT.encode("utf-8"))
        cues = parse_and_validate(path)
        self.assertEqual(len(cues), 2)

    def test_multiline_cue_text_is_preserved(self):
        content = "1\n00:00:01,000 --> 00:00:03,000\nLine one.\nLine two.\n"
        path = _write(self.dir, content)
        cues = parse_and_validate(path)
        self.assertEqual(cues[0].text, "Line one.\nLine two.")

    def test_blank_lines_between_cues_do_not_break_parsing(self):
        content = VALID_SRT + "\n\n"  # trailing blank block
        path = _write(self.dir, content)
        cues = parse_and_validate(path)
        self.assertEqual(len(cues), 2)

    def test_empty_cue_text_is_skipped_not_fatal(self):
        content = (
            "1\n00:00:01,000 --> 00:00:02,000\n\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nReal text.\n"
        )
        path = _write(self.dir, content)
        cues = parse_and_validate(path)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "Real text.")

    def test_malformed_timestamp_is_rejected(self):
        content = "1\nnot a timestamp\nHello.\n"
        path = _write(self.dir, content)
        with self.assertRaises(SrtValidationError):
            parse_and_validate(path)

    def test_reversed_timestamp_is_rejected(self):
        content = "1\n00:00:05,000 --> 00:00:02,000\nHello.\n"
        path = _write(self.dir, content)
        with self.assertRaises(SrtValidationError):
            parse_and_validate(path)

    def test_non_integer_cue_number_is_rejected(self):
        content = "one\n00:00:01,000 --> 00:00:02,000\nHello.\n"
        path = _write(self.dir, content)
        with self.assertRaises(SrtValidationError):
            parse_and_validate(path)

    def test_cue_with_no_text_line_at_all_is_treated_as_an_empty_cue(self):
        # Same real-world shape as an explicit blank text line -- skipped,
        # not a validation error.
        content = "1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\nText.\n"
        path = _write(self.dir, content)
        cues = parse_and_validate(path)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "Text.")

    def test_block_missing_the_timestamp_line_entirely_is_rejected(self):
        content = "1\n"  # cue number only, nothing else in the block
        path = _write(self.dir, content)
        with self.assertRaises(SrtValidationError):
            parse_and_validate(path)

    def test_end_equal_to_start_is_accepted_not_reversed(self):
        content = "1\n00:00:01,000 --> 00:00:01,000\nInstant.\n"
        path = _write(self.dir, content)
        cues = parse_and_validate(path)
        self.assertEqual(len(cues), 1)

    def test_oversized_source_file_rejected_before_reading(self):
        # Real gap this closes (production-readiness audit, 2026-09-21):
        # a browser-uploaded source was already capped at 2 MiB
        # (api.py), but a source_srt_path pointed at the media library
        # had no size limit at all.
        import srt_translation
        path = _write(self.dir, VALID_SRT)
        with unittest.mock.patch.object(srt_translation, "MAX_SRT_FILE_BYTES", 1):
            with self.assertRaises(SrtValidationError):
                parse_and_validate(path)


class DetectSourceLanguageTests(unittest.TestCase):
    def test_clearly_english_text_detects_as_en_with_high_confidence(self):
        from srt_translation import ValidatedCue, _detect_source_language
        cues = [ValidatedCue(1, 0.0, 2.0,
                             "This is a perfectly ordinary English sentence about the weather today.")]
        language, probability = _detect_source_language(cues)
        self.assertEqual(language, "en")
        self.assertGreater(probability, 0.5)

    def test_empty_cue_list_returns_undetectable_not_a_crash(self):
        from srt_translation import _detect_source_language
        language, probability = _detect_source_language([])
        self.assertEqual(language, "und")
        self.assertEqual(probability, 0.0)


if __name__ == "__main__":
    unittest.main()
