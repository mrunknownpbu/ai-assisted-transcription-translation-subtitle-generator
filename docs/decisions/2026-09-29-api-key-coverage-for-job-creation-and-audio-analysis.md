# API-key coverage for job creation and audio analysis (2026-09-29)

`require_api_key()` now uses `hmac.compare_digest()` on UTF-8 bytes and
guards `POST /api/jobs`, `POST /api/srt-uploads`, and
`GET /api/audio-streams` as well as the previously guarded mutating
routes. This protects GPU-consuming stream analysis too. Configured-key
tests reject missing credentials on all three new routes, prove the audio
model path is not reached, and verify the matching key uses the
constant-time comparison. API tests: 148 passed, 2 subtests.

Production has `SUBTITLE_AI_API_KEY` unset, so the configured-key 401
path is covered by tests rather than live configuration. A production
S01E01 POST completed under the unchanged open-by-default configuration
with existing-output KEEP semantics (`outputs: []`); the backed-up
`.ja.srt` and `.en.srt` remained byte-identical. Backups:
`/cache/verification-backups/20260929-api-key-guard/`.
