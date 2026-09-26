"""Uploading a .vtt from the PC (POST /api/srt-uploads).

A WebVTT file named .srt was already readable (see test_webvtt.py); this
lets the user upload it under its real .vtt name too. It is stored as
<uuid>.srt and converted when read, exactly like the .srt-named case.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api
from srt_translation import parse_and_validate

VTT = (
    "WEBVTT\n\n"
    "00:00:12.333 --> 00:00:17.542 align:start\n"
    "(ｱﾄﾞ)私をクビにするなんてさ\n\n"
    "00:00:21.208 --> 00:00:23.125\nカズさ\n"
).encode("utf-8")


class VttUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "media"
        (self.root / "Show").mkdir(parents=True)
        (self.root / "Show" / "S01E01.mkv").write_bytes(b"not a real video, just needs to exist")
        self.upload_dir = Path(self.tmp.name) / "srt_uploads"  # deliberately OUTSIDE the media root
        app = api.create_app(Path(self.tmp.name) / "jobs.db", str(self.root),
                             srt_upload_dir=str(self.upload_dir))
        self.client = TestClient(app)

    def _upload(self, name, data=VTT):
        return self.client.post("/api/srt-uploads", files={"file": (name, data, "text/vtt")})

    def test_vtt_extension_is_accepted_and_keeps_its_display_name(self):
        r = self._upload("Episode 03.vtt")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["filename"], "Episode 03.vtt")

    def test_extension_check_is_case_insensitive(self):
        self.assertEqual(self._upload("EPISODE.VTT").status_code, 201)

    def test_stored_under_a_uuid_srt_name_and_readable_by_the_translation_parser(self):
        upload_id = self._upload("ep.vtt").json()["upload_id"]
        stored = self.upload_dir / f"{upload_id}.srt"
        self.assertTrue(stored.is_file())
        cues = parse_and_validate(stored)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0].start, 12.333)
        self.assertEqual(cues[1].text, "カズさ")

    def test_upload_id_is_usable_to_queue_a_translation_job(self):
        upload_id = self._upload("ep.vtt").json()["upload_id"]
        r = self.client.post("/api/srt-translations", json={
            "video_path": "Show/S01E01.mkv", "source_upload_id": upload_id})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["job"]["source_is_uploaded"])

    def test_srt_still_accepted(self):
        self.assertEqual(self._upload("ep.srt", b"1\n00:00:00,000 --> 00:00:01,000\nhi\n").status_code, 201)

    def test_other_extensions_still_rejected_and_the_message_names_both(self):
        r = self._upload("notes.txt", b"hello")
        self.assertEqual(r.status_code, 400)
        self.assertIn(".srt or .vtt", r.json()["detail"])

    def test_a_path_in_the_filename_is_still_stripped(self):
        r = self._upload("../../etc/evil.vtt")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["filename"], "evil.vtt")
        # Nothing was written outside the upload directory.
        self.assertFalse((Path(self.tmp.name).parent / "etc").exists())

    def test_non_utf8_vtt_is_still_rejected(self):
        self.assertEqual(self._upload("bad.vtt", b"WEBVTT\n\n\xff\xfe\xfa").status_code, 400)


if __name__ == "__main__":
    unittest.main()
