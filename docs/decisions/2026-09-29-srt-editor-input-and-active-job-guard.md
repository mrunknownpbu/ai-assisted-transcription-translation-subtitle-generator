# SRT editor input and active-job guard (2026-09-29)

`PUT /api/jobs/{id}/srt` now rejects blank cue lines, embedded CR/LF, and
the SRT timing arrow (`-->`) in each submitted text line; these could
otherwise create extra cues or timing syntax when rendered. It also
returns 409 unless the addressed job is completed, so editing a retry
cannot race its output write. Regression tests confirm malformed edits
leave the subtitle file unchanged and that an active retry is not editable.
The API tests pass (144 tests, 2 subtests); the full suite passes (1134
tests, 40 subtests). Deployed and checked on the production API: PUT to
the active Hammer Session! S01E01 job returned 409, and a cue-injection
payload returned 422. The required POST job completed, but its pipeline
result was unsuitable for comparison: AUTO detected Korean at 39.9%
confidence despite audio inspection selecting Japanese at 95.5%, wrote
`.ko.srt`, and changed `.en.srt`. Restored `.en.srt` byte-for-byte from
the pre-run backup (SHA-256
`34aac0df09db4375a09aef6a57c9254f1b29b918121af274b60127f164d55a58`),
confirmed `.ja.srt` unchanged
(`5b967a999a0b422704d936e0f3dd4222730467eb86a4d76cbafa1844bf5f1665`),
and removed the generated `.ko.srt`, which was absent before the run. Backup:
`/cache/verification-backups/20260929-srt-editor-hardening/`.
