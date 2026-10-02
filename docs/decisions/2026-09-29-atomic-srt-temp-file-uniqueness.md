# Atomic SRT temp-file uniqueness (2026-09-29)

`write_srt_atomic()` now creates a randomized temporary file with
`tempfile.mkstemp()` in the target directory, so a stale `.tmp<PID>` left
by a crash cannot block every later write when uvicorn reuses PID 1.
The temp remains on the target filesystem for atomic replacement and is
removed in `finally`; output permissions remain 0644. A regression test
pre-creates the old fixed-name temp and confirms a write succeeds without
altering it. Full suite: 1131 passed, 38 subtests. Verified with a
production POST `/api/jobs` cache-hit rerun of Hammer Session! S01E01
(17.4s, validation passed): both `.ja.srt` and `.en.srt` were atomically
written, matched their pre-run backups byte-for-byte, retained mode 0644,
and left no temp files. Backups:
`/cache/verification-backups/20260929-atomic-srt-temp/`.
