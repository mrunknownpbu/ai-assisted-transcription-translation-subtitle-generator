# Fire failure webhooks only on transition into failed (2026-09-29)

Before the change, a failed job's webhook fired again on a later
`append_log()` or metadata update (measured two calls for one failure
plus one later log update). `JobStore.update()` now captures the
previous status and committed failed-job snapshot in the same
`BEGIN IMMEDIATE` transaction, and a dedicated callback fires only when
the row changes from a non-failed status to `failed`. The existing
per-mutation GUI event callback remains unchanged. Regression coverage
confirms one notification across later log, field, and repeated-failed
updates. Full suite: 1159 passed, 40 subtests.

After deployment, a production Hammer Session! S01E01 POST completed
with KEEP semantics (`outputs: []`); `.ja.srt`, `.en.srt`, and
`.en.hi.srt` matched their pre-run backups byte-for-byte. The production
rerun was successful, so the failure-only webhook itself was exercised
in tests rather than by forcing a production job to fail. Backup:
`/cache/verification-backups/20260929-webhook-transition/`.
