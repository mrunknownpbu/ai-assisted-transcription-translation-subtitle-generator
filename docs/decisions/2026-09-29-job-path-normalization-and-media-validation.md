# Job path normalization and media validation (2026-09-29)

Before the change, `JobStore.create()` accepted both `Show/S01E01.mkv`
and `Show/./S01E01.mkv` as active jobs, and `POST /api/jobs` returned
201 for both a missing `.mkv` and an existing `.nfo`. The store now
normalizes lexical path components; the API requires an existing,
root-contained supported video file and passes its canonical path
relative to the media root into the store. Regression coverage includes
path aliases, missing files, and non-video files. Full suite: 1137 passed,
40 subtests.

Production verification: with Hammer Session! S01E01 `.ja.srt` and
`.en.srt` backed up, the API rejected a missing video and an `.nfo` with
400, accepted a `./` plus `..` alias and returned its canonical
media-root-relative path, then rejected a concurrent POST of the
canonical path with 409. The job completed with existing-output KEEP
semantics (`outputs: []`); both SRTs matched their backups byte-for-byte.
Backups:
`/cache/verification-backups/20260929-job-path-validation/`.
