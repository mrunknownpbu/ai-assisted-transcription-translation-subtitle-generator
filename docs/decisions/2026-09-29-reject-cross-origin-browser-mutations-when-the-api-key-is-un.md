# Reject cross-origin browser mutations when the API key is unset (2026-09-29)

Before the change, production `POST /api/jobs/{id}/cancel` and
`/retry` requests with an attacker `Origin` and
`Sec-Fetch-Site: cross-site` reached job lookup (404 for a nonexistent
test ID), confirming no browser-origin check. With no
`SUBTITLE_AI_API_KEY`, unsafe requests now reject cross-site Fetch
Metadata and mismatched/malformed `Origin` values with 403. Same-origin
browser calls and headerless non-browser clients remain usable; a
configured API key retains its existing credential check. Tests cover
cancel/retry, unchanged job state, and same-origin/headerless behavior.
Full suite: 1162 passed, 40 subtests.

After deployment, the same cross-site cancel/retry probes returned 403,
while a same-origin probe reached normal lookup (404). A production
Hammer Session! S01E01 POST completed with KEEP semantics (`outputs:
[]`); `.ja.srt`, `.en.srt`, and `.en.hi.srt` matched their pre-run
backups byte-for-byte. Backup:
`/cache/verification-backups/20260929-csrf-origin-guard/`.
