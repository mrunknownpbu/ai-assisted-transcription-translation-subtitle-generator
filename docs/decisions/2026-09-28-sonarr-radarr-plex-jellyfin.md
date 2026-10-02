# Sonarr / Radarr / Plex / Jellyfin (since 2026-09-28)

All four see the library at the same `/data/media/...` paths as this app
(verified live), so nothing translates paths.

- **`arr_client.py`**: Sonarr/Radarr decide which series or movie a file
  is (longest matching folder), with its TVDB/TMDB/IMDb/TVmaze ids and
  original language. `glossary_profile.find_tvdb_id()` asks Sonarr FIRST
  and falls back to the `{tvdb-<id>}` tag, because the tag is wrong in
  practice: 3 of 1,984 series have a broken tag (`{tvbd-...}`, a stray
  space), and 6 disagree with Sonarr. New jobs for those 6 get Sonarr's
  id, so their old jobs (tagged id) and new ones show as two series. The
  index is served stale while it refreshes (Radarr's list takes ~8s) and is
  persisted in `/cache/arr`, so never put a blocking fetch back into
  `get()`.
- **Movies** have a glossary file keyed `tmdb_movie_id` and film-wide cast
  protection (TMDB movie credits). As of 2026-09-28 no movie in the library
  has a genuine original-language subtitle: every `.hi.srt` checked turned
  out to be English (hearing-impaired). That's why `source_subtitles()`
  checks a file's TEXT with langdetect rather than trusting its name.
- **`media_servers.py`**: after a job commits files, Plex gets a partial
  scan of that one folder and Jellyfin gets `/Library/Media/Updated`. This
  runs on a background thread, and the outcome goes to the job log
  (`MEDIA_SERVERS_NOTIFIED`). No thread starts when neither server is
  configured; this matters for tests, where a late log write raced the
  temp-dir cleanup.
- MDBList was considered and not used: it has ids and ratings but no
  character data, and Sonarr/Radarr already supply the ids.
