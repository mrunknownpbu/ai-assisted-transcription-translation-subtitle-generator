"""Series page fixes (2026-09-26 screenshots):

1. A series with no glossary `title:` (and no TVDB lookup) showed as a bare
   "Series #449837" even though its folder is named
   "Happy Kanako's Killer Life (2025) {tvdb-449837}". The folder name is now
   the fallback.
2. The Series card said "294 episodes" for a 39-episode show: it printed the
   number of JOB rows, and every retry/re-run is its own row. `episodes` is
   now the count of distinct files; `total` stays the job count.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api
import glossary_profile
from jobstore import JobStore


class TitleFromVideoPathTests(unittest.TestCase):
    def test_strips_the_tvdb_tag_from_the_series_folder(self):
        p = "media/drama/japanese/Happy Kanako's Killer Life (2025) {tvdb-449837}/Season 02/S02E03.mkv"
        self.assertEqual(glossary_profile.title_from_video_path(p), "Happy Kanako's Killer Life (2025)")

    def test_tag_position_and_case_do_not_matter(self):
        self.assertEqual(glossary_profile.title_from_video_path("{TVDB-5} Show/S01E01.mkv"), "Show")

    def test_none_when_the_path_has_no_tvdb_tag(self):
        self.assertIsNone(glossary_profile.title_from_video_path("Untagged/S01E01.mkv"))

    def test_none_when_the_folder_is_only_the_tag(self):
        self.assertIsNone(glossary_profile.title_from_video_path("{tvdb-5}/S01E01.mkv"))

    def test_uses_the_series_folder_not_the_season_folder(self):
        p = "Show (2020) {tvdb-1}/Season 01/S01E01.mkv"
        self.assertEqual(glossary_profile.title_from_video_path(p), "Show (2020)")


class SeriesListApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.glossary_dir = Path(self.tmp.name) / "glossary"
        self.glossary_dir.mkdir()
        (self.glossary_dir / "titled.yaml").write_text(
            "tvdb_id: 111\ntitle: Curated Title\nentities: []\n", encoding="utf-8")
        app = api.create_app(Path(self.tmp.name) / "jobs.db", glossary_dir=str(self.glossary_dir))
        self.client = TestClient(app)

    def _post_and_finish(self, video_path):
        """Queue a job and cancel it, so the SAME video can be queued again:
        the store refuses a second ACTIVE job for one video (409), so a re-run
        or retry only ever exists after the previous job has finished."""
        r = self.client.post("/api/jobs", json={"video_path": video_path})
        self.assertEqual(r.status_code, 201, r.text)
        job_id = r.json()["job"]["id"]
        self.assertEqual(self.client.post(f"/api/jobs/{job_id}/cancel").status_code, 200)

    def _entry(self, tvdb_id):
        series = self.client.get("/api/series").json()["series"]
        return next(s for s in series if s["tvdb_id"] == tvdb_id)

    def test_untitled_series_falls_back_to_its_folder_name(self):
        self.client.post("/api/jobs", json={"video_path": "Happy Show (2025) {tvdb-222}/Season 01/S01E01.mkv"})
        self.assertEqual(self._entry(222)["title"], "Happy Show (2025)")

    def test_detail_page_gets_the_same_fallback_title(self):
        self.client.post("/api/jobs", json={"video_path": "Happy Show (2025) {tvdb-222}/Season 01/S01E01.mkv"})
        self.assertEqual(self.client.get("/api/series/222").json()["title"], "Happy Show (2025)")

    def test_glossary_title_still_wins_over_the_folder_name(self):
        self.client.post("/api/jobs", json={"video_path": "Folder Name {tvdb-111}/S01E01.mkv"})
        self.assertEqual(self._entry(111)["title"], "Curated Title")

    def test_untagged_bucket_still_has_no_title(self):
        self.client.post("/api/jobs", json={"video_path": "Untagged/S01E01.mkv"})
        self.assertIsNone(self._entry(None)["title"])

    def test_series_with_no_glossary_dir_configured_still_gets_a_title(self):
        app = api.create_app(Path(self.tmp.name) / "other.db")
        client = TestClient(app)
        client.post("/api/jobs", json={"video_path": "Solo {tvdb-333}/S01E01.mkv"})
        entry = next(s for s in client.get("/api/series").json()["series"] if s["tvdb_id"] == 333)
        self.assertEqual(entry["title"], "Solo")

    def test_episodes_counts_distinct_files_while_total_counts_jobs(self):
        # Three jobs, two distinct episode files (E01 was re-run).
        self._post_and_finish("Show {tvdb-444}/S01E01.mkv")
        self._post_and_finish("Show {tvdb-444}/S01E01.mkv")
        self._post_and_finish("Show {tvdb-444}/S01E02.mkv")
        entry = self._entry(444)
        self.assertEqual(entry["total"], 3)
        self.assertEqual(entry["episodes"], 2)

    def test_episodes_is_per_series(self):
        self.client.post("/api/jobs", json={"video_path": "A {tvdb-1}/S01E01.mkv"})
        self.client.post("/api/jobs", json={"video_path": "B {tvdb-2}/S01E01.mkv"})
        self.client.post("/api/jobs", json={"video_path": "B {tvdb-2}/S01E02.mkv"})
        self.assertEqual(self._entry(1)["episodes"], 1)
        self.assertEqual(self._entry(2)["episodes"], 2)

    def test_untagged_bucket_reports_episodes_too(self):
        self._post_and_finish("x/S01E01.mkv")
        self._post_and_finish("x/S01E01.mkv")
        entry = self._entry(None)
        self.assertEqual((entry["total"], entry["episodes"]), (2, 1))


class LatestVideoPathTests(unittest.TestCase):
    def test_returns_none_for_an_unknown_series(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(JobStore(Path(d) / "jobs.db").latest_video_path(999))


if __name__ == "__main__":
    unittest.main()
