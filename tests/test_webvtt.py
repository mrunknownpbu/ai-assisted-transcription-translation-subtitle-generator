"""WebVTT input for the SRT-translation workflow.

Real failure (2026-09-26, Happy Kanako S02E03): a streaming-service release
ships its subtitle as WebVTT but named ".srt". The strict SRT reader rejected
it at the header ("malformed cue block ... 'WEBVTT'"), both when picked from
the library and when uploaded from the PC. It is now converted to SRT text
before validation.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import srt
from srt_translation import SrtValidationError, parse_and_validate


def _write(content: str, *, bom: bool = False, crlf: bool = False) -> Path:
    text = content.replace("\n", "\r\n") if crlf else content
    data = (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")
    fd = tempfile.NamedTemporaryFile(suffix=".srt", delete=False)
    fd.write(data)
    fd.close()
    return Path(fd.name)


# The shape of the real failing file: BOM + CRLF, hh:mm:ss.mmm timings,
# half-width katakana, a two-line cue, no identifiers/tags.
REAL_SHAPE = (
    "WEBVTT\n\n"
    "00:00:12.333 --> 00:00:17.542\n"
    "(ｱﾄﾞ)私をクビにするなんてさ\nセンスないよね 社長\n\n"
    "00:00:21.208 --> 00:00:23.125\nカズさ\n\n"
)


class IsWebvttTests(unittest.TestCase):
    def test_header_variants(self):
        self.assertTrue(srt.is_webvtt("WEBVTT\n\n"))
        self.assertTrue(srt.is_webvtt("WEBVTT - some title\n"))
        self.assertTrue(srt.is_webvtt("WEBVTT"))

    def test_not_webvtt(self):
        self.assertFalse(srt.is_webvtt("1\n00:00:01,000 --> 00:00:02,000\nhi\n"))
        self.assertFalse(srt.is_webvtt("WEBVTTX\n"))  # not the signature
        self.assertFalse(srt.is_webvtt(""))


class WebvttToSrtTests(unittest.TestCase):
    def test_basic_conversion_renumbers_and_uses_comma_milliseconds(self):
        out = srt.webvtt_to_srt("WEBVTT\n\n00:00:12.333 --> 00:00:17.542\nHello\n\n00:00:21.208 --> 00:00:23.125\nBye\n")
        self.assertEqual(
            out,
            "1\n00:00:12,333 --> 00:00:17,542\nHello\n\n2\n00:00:21,208 --> 00:00:23,125\nBye\n",
        )

    def test_minutes_only_timestamps_get_an_hour(self):
        out = srt.webvtt_to_srt("WEBVTT\n\n01:02.500 --> 01:04.000\nhi\n")
        self.assertIn("00:01:02,500 --> 00:01:04,000", out)

    def test_cue_identifiers_and_settings_are_dropped(self):
        out = srt.webvtt_to_srt(
            "WEBVTT\n\nintro\n00:00:01.000 --> 00:00:02.000 align:start position:0%\nhello\n")
        self.assertEqual(out, "1\n00:00:01,000 --> 00:00:02,000\nhello\n")

    def test_note_style_and_region_blocks_are_skipped(self):
        out = srt.webvtt_to_srt(
            "WEBVTT\n\nNOTE a comment\nspanning lines\n\nSTYLE\n::cue { color: red }\n\n"
            "REGION\nid:r1\n\n00:00:01.000 --> 00:00:02.000\nreal\n")
        self.assertEqual(out, "1\n00:00:01,000 --> 00:00:02,000\nreal\n")

    def test_markup_is_stripped_and_entities_decoded(self):
        out = srt.webvtt_to_srt(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Kanako><c.yellow>Tom &amp; Jerry</c>&nbsp;<i>now</i>\n")
        self.assertIn("Tom & Jerry now", out)
        self.assertNotIn("<", out)

    def test_escaped_angle_brackets_are_text_not_markup(self):
        out = srt.webvtt_to_srt("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n1 &lt; 2\n")
        self.assertIn("1 < 2", out)

    def test_block_without_timing_line_is_rejected_not_skipped(self):
        with self.assertRaises(ValueError):
            srt.webvtt_to_srt("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nok\n\njust some text\nno timing\n")


class ParseAndValidateAcceptsWebvttTests(unittest.TestCase):
    def test_real_shape_bom_and_crlf(self):
        path = _write(REAL_SHAPE, bom=True, crlf=True)
        cues = parse_and_validate(path)
        self.assertEqual(len(cues), 2)
        self.assertEqual([c.number for c in cues], [1, 2])
        self.assertAlmostEqual(cues[0].start, 12.333)
        self.assertAlmostEqual(cues[0].end, 17.542)
        self.assertEqual(cues[0].text, "(ｱﾄﾞ)私をクビにするなんてさ\nセンスないよね 社長")
        self.assertEqual(cues[1].text, "カズさ")

    def test_result_round_trips_to_a_real_srt(self):
        cues = parse_and_validate(_write(REAL_SHAPE))
        rendered = srt.render(cues)
        self.assertTrue(rendered.startswith("1\n00:00:12,333 --> 00:00:17,542\n"))
        self.assertNotIn("WEBVTT", rendered)

    def test_reversed_timing_in_vtt_is_still_rejected(self):
        with self.assertRaises(SrtValidationError):
            parse_and_validate(_write("WEBVTT\n\n00:00:05.000 --> 00:00:01.000\nx\n"))

    def test_malformed_vtt_block_surfaces_as_srt_validation_error(self):
        with self.assertRaises(SrtValidationError):
            parse_and_validate(_write("WEBVTT\n\nno timing here\nat all\n"))

    def test_plain_srt_is_unchanged(self):
        cues = parse_and_validate(_write("1\n00:00:01,000 --> 00:00:02,500\nhello\n\n2\n00:00:03,000 --> 00:00:04,000\nbye\n"))
        self.assertEqual([c.text for c in cues], ["hello", "bye"])
        self.assertAlmostEqual(cues[0].end, 2.5)

    def test_genuinely_broken_srt_still_fails(self):
        with self.assertRaises(SrtValidationError):
            parse_and_validate(_write("totally not a subtitle\nfile\n"))


if __name__ == "__main__":
    unittest.main()
