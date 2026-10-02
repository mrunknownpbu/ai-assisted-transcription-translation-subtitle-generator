# Match bracketed sibling subtitle names in `/api/media` (2026-09-29)

Before the fix, `/api/media` returned an empty `existing_subtitles`
list for `[Grp] Ep 01.mkv` even with matching `.tr.srt` and `.en.srt`
siblings: `Path.glob()` interpreted the brackets as a character class.
The endpoint now shares `/api/browse`'s exact-name sibling scan, including
its root-containment and file checks. Regression coverage exercises both
subtitle variants on a bracketed filename. Full suite: 1152 passed,
40 subtests.

After deployment, `/api/media` on production Hammer Session! S01E01
returned its `.ja.srt`, `.en.srt`, and `.en.hi.srt` siblings. A production
POST `/api/jobs` completed with KEEP semantics (`outputs: []`); the
backed-up `.ja.srt`, `.en.srt`, and `.en.hi.srt` files remained
byte-identical. Backup:
`/cache/verification-backups/20260929-media-subtitle-discovery/`.
