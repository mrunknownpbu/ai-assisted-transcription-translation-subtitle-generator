"""Sonarr/Radarr as the source of ids (arr_client), Plex/Jellyfin refresh
after output (media_servers), and movies in cast-name protection
(2026-09-28). Real library facts behind the tests: Sonarr and the media
servers see /data/media/... exactly as this app does; 3 Sonarr series have
broken {tvdb-} tags and 6 have tags that disagree with Sonarr."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

import arr_client
import cast_enrichment as ce
import cast_metadata as cm
import glossary_profile as gp
import media_servers

SERIES = [
    {"id": 9, "title": "Love Is In The Air (2020)", "year": 2020, "tvdbId": 383383, "tmdbId": 104877,
     "imdbId": "tt12439466", "tvMazeId": 48595, "originalLanguage": {"name": "Turkish"},
     "path": "/data/media/drama/turkish/Love Is In The Air (2020) {tvdb-383383}"},
    {"id": 10, "title": "Ex-boyfriend & boss", "tvdbId": 443608, "originalLanguage": {"name": "Chinese"},
     "path": "/data/media/drama/chinese/Ex-Boyfriend & Boss (2023) {tvbd-443608}"},
    {"id": 11, "title": "Re-matched", "tvdbId": 999, "originalLanguage": {"name": "Klingon"},
     "path": "/data/media/tv_series/Re-matched {tvdb-111}"},
]
MOVIES = [{"id": 1, "title": "10 Cloverfield Lane", "year": 2016, "tmdbId": 333371, "imdbId": "tt1179933",
           "originalLanguage": {"name": "English"},
           "path": "/data/media/movies/10 Cloverfield Lane (2016) {tmdb-333371}"}]


def _resp(payload, status=200):
    r = MagicMock(status_code=status)
    r.json.return_value = payload
    r.raise_for_status.side_effect = None if status < 400 else __import__("httpx").HTTPStatusError(
        "x", request=MagicMock(), response=MagicMock())
    return r


class ArrTestCase(unittest.TestCase):
    ENV = {"SONARR_URL": "http://sonarr", "SONARR_API_KEY": "s", "RADARR_URL": "http://radarr",
           "RADARR_API_KEY": "r", "SUBTITLE_AI_MEDIA_ROOT": "/data"}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name)
        for p in (patch.dict(os.environ, self.ENV), patch.object(arr_client, "CACHE_DIR", self.cache),
                  patch.object(arr_client, "_indexes", {"series": arr_client._Index("series"),
                                                        "movie": arr_client._Index("movie")})):
            p.start()
            self.addCleanup(p.stop)

    def get(self, url, headers=None, timeout=None):
        return _resp(SERIES if url.endswith("/series") else MOVIES)


class ArrLookupTests(ArrTestCase):
    def test_file_maps_to_its_series_with_all_ids_and_language(self):
        with patch("arr_client.httpx.get", side_effect=self.get):
            info = arr_client.lookup("media/drama/turkish/Love Is In The Air (2020) {tvdb-383383}/Season 01/x S01E30.mkv")
        self.assertEqual(info.kind, "series")
        self.assertEqual(info.ids, {"tvdb": 383383, "tmdb": 104877, "imdb": "tt12439466", "tvmaze": 48595})
        self.assertEqual(info.original_language, "tr")

    def test_movie_and_unknown_language(self):
        with patch("arr_client.httpx.get", side_effect=self.get):
            movie = arr_client.lookup("/data/media/movies/10 Cloverfield Lane (2016) {tmdb-333371}/a.mkv")
            odd = arr_client.lookup("/data/media/tv_series/Re-matched {tvdb-111}/S01E01.mkv")
        self.assertEqual((movie.kind, movie.ids["tmdb"], movie.original_language), ("movie", 333371, "en"))
        self.assertIsNone(odd.original_language)

    def test_folder_prefix_must_match_a_whole_folder(self):
        with patch("arr_client.httpx.get", side_effect=self.get):
            self.assertIsNone(arr_client.lookup("/data/media/movies/10 Cloverfield Lane (2016) {tmdb-333371}X/a.mkv"))

    def test_sonarr_beats_a_broken_or_stale_folder_tag(self):
        with patch("arr_client.httpx.get", side_effect=self.get):
            self.assertEqual(gp.find_tvdb_id("/data/media/drama/chinese/Ex-Boyfriend & Boss (2023) {tvbd-443608}/S01E01.mkv"), 443608)
            self.assertEqual(gp.find_tvdb_id("/data/media/tv_series/Re-matched {tvdb-111}/S01E01.mkv"), 999)
            self.assertEqual(gp.title_from_video_path("/data/media/tv_series/Re-matched {tvdb-111}/S01E01.mkv"),
                             "Re-matched")

    def test_outage_falls_back_to_last_good_index_on_disk(self):
        with patch("arr_client.httpx.get", side_effect=self.get):
            arr_client.lookup("/data/media/movies/x")
        arr_client._indexes["series"] = arr_client._Index("series")   # fresh process
        with patch("arr_client.httpx.get", side_effect=__import__("httpx").ConnectError("down")) as get:
            info = arr_client.lookup("/data/media/drama/turkish/Love Is In The Air (2020) {tvdb-383383}/a.mkv")
        self.assertEqual(info.tvdb_id, 383383)
        get.assert_not_called()  # served from disk, no network wait

    def test_unconfigured_is_a_no_op_and_the_tag_still_works(self):
        with patch.dict(os.environ, {"SONARR_URL": "", "RADARR_URL": ""}), \
             patch("arr_client.httpx.get") as get:
            self.assertIsNone(arr_client.lookup("/data/media/x"))
            self.assertEqual(gp.find_tvdb_id("Show {tvdb-42}/S01E01.mkv"), 42)
        get.assert_not_called()


class MediaServerTests(unittest.TestCase):
    ENV = {"PLEX_URL": "http://plex:32400", "PLEX_TOKEN": "t", "JELLYFIN_URL": "http://jf:8096",
           "JELLYFIN_API_KEY": "k", "SUBTITLE_AI_MEDIA_REFRESH": ""}
    SECTIONS = {"MediaContainer": {"Directory": [
        {"key": "1", "Location": [{"path": "/data/media/drama/turkish"}]},
        {"key": "3", "Location": [{"path": "/data/media/movies"}]}]}}
    FILE = "/data/media/drama/turkish/Show {tvdb-1}/Season 01/Show S01E04.en.srt"

    def setUp(self):
        p = patch.dict(media_servers._plex_sections, {"at": 0.0, "items": []})
        p.start()
        self.addCleanup(p.stop)

    def test_targeted_refresh_on_both_servers(self):
        with patch.dict(os.environ, self.ENV), \
             patch("media_servers.httpx.get", side_effect=[_resp(self.SECTIONS), _resp({})]) as get, \
             patch("media_servers.httpx.post", return_value=_resp({}, 204)) as post:
            results = media_servers.notify([self.FILE])
        self.assertEqual(results["plex"], "scanned folder in section 1")
        self.assertEqual(get.call_args_list[1].kwargs["params"], {"path": str(Path(self.FILE).parent)})
        self.assertIn("/library/sections/1/refresh", get.call_args_list[1].args[0])
        self.assertEqual(post.call_args.kwargs["json"]["Updates"][0]["Path"], self.FILE)
        self.assertIn('Token="k"', post.call_args.kwargs["headers"]["Authorization"])

    def test_a_down_server_is_reported_not_raised(self):
        import httpx
        with patch.dict(os.environ, self.ENV), \
             patch("media_servers.httpx.get", side_effect=httpx.ConnectError("down")), \
             patch("media_servers.httpx.post", return_value=_resp({}, 204)):
            results = media_servers.notify([self.FILE])
        self.assertTrue(results["plex"].startswith("failed"))
        self.assertEqual(results["jellyfin"], "notified 1 file(s)")

    def test_off_switch_and_unconfigured(self):
        with patch.dict(os.environ, {**self.ENV, "SUBTITLE_AI_MEDIA_REFRESH": "off"}):
            self.assertFalse(media_servers.configured())
            self.assertEqual(media_servers.notify([self.FILE]), {})
        with patch.dict(os.environ, {k: "" for k in self.ENV}):
            self.assertFalse(media_servers.configured())


class TvmazeNicknameTests(unittest.TestCase):
    def test_parenthesised_nickname_vs_annotation(self):
        self.assertEqual(cm.parse_character("Melek Yücel (Melo)"), ("Melek", "Melek Yücel", ["Melo"]))
        self.assertEqual(cm.parse_character("Eda (voice)"), ("Eda", "Eda", []))
        self.assertEqual(cm.parse_character("Eda (young)"), ("Eda", "Eda", []))


class MovieGlossaryTests(unittest.TestCase):
    def test_movie_file_is_its_own_layer_and_not_global(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "movie.yaml").write_text(yaml.safe_dump(
                {"tmdb_movie_id": 5, "entities": [{"canonical": "Ha-na", "protected": True}]}), encoding="utf-8")
            Path(tmp, "global.yaml").write_text(yaml.safe_dump(
                {"entities": [{"canonical": "Seoul", "protected": True}]}), encoding="utf-8")
            names = lambda **kw: sorted(e.canonical for e in gp.load_profile(tmp, enrich_from_tvdb=False, **kw).entities)
            self.assertEqual(names(tmdb_movie_id=5), ["Ha-na", "Seoul"])
            self.assertEqual(names(tmdb_movie_id=6), ["Seoul"])
            self.assertEqual(names(tvdb_id=5), ["Seoul"])


class EnrichMovieTests(unittest.TestCase):
    def test_film_wide_protection_in_a_new_movie_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            movie_dir = tmp / "Film (2020) {tmdb-5}"
            movie_dir.mkdir()
            body = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\nSonra Hana geldi {i}.\n\n" for i in range(1, 7))
            (movie_dir / "Film (2020).ko.srt").write_text(body, encoding="utf-8")
            (movie_dir / "Film (2020).en.srt").write_text(body, encoding="utf-8")
            glossary = tmp / "glossary"
            glossary.mkdir()
            book = cm.CastBook()
            book.add("Hana Kim", "tmdb", None)
            with patch.object(ce.glossary_files, "commit", return_value=True), \
                 patch.object(ce, "_text_language", return_value="ko"):
                report = ce.enrich_movie(5, movie_dir, glossary, book=book,
                                         probe=lambda lines, lang: [l.replace("Hana", "one") for l in lines])
            self.assertEqual((report["language"], report["added"], report["key"]), ("ko", ["Hana"], "movie-5"))
            data = yaml.safe_load((glossary / "movie-5.yaml").read_text())
            self.assertEqual(data["tmdb_movie_id"], 5)
            entry = data["entities"][0]
            self.assertNotIn("episodes", entry)   # film-wide
            self.assertTrue(entry["case_sensitive"])

    def test_movie_reports_are_keyed_separately_from_series(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cm, "CACHE_DIR", Path(tmp)):
            ce.save_report({"key": "movie-5", "checked_at": 1.0})
            ce.save_report({"key": "tvdb-5", "tvdb_id": 5, "checked_at": 2.0})
            self.assertEqual(ce.load_report("movie-5")["checked_at"], 1.0)
            self.assertEqual(ce.load_report(5)["checked_at"], 2.0)


if __name__ == "__main__":
    unittest.main()
