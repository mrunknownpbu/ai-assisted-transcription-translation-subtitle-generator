# SRT upload retention (2026-09-29)

Before the change, a 30-day-old unreferenced UUID `.srt` remained after
the worker's hourly cleanup sweep, which only handled work directories.
The same startup/hourly sweep now removes upload files older than seven
days unless a queued/running uploaded-source job still references them.
It ignores symlinks, directories, and non-upload filenames, and logs
per-file failures without stopping the sweep. Tests cover age, active-job
protection, terminal-job expiration, path filtering, and worker
integration. Full suite: 1149 passed, 40 subtests.

There were no existing production uploads before deployment. An
eight-day-old UUID fixture was removed by the production worker's startup
sweep. A production Hammer Session! S01E01 POST completed with existing
output KEEP semantics (`outputs: []`); `.ja.srt` and `.en.srt` matched
their pre-run backups byte-for-byte. Backup:
`/cache/verification-backups/20260929-upload-cleanup/`.
