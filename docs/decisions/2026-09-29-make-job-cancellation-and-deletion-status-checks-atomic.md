# Make job cancellation and deletion status checks atomic (2026-09-29)

Before the change, a controlled interleaving let `request_cancel()` read
`queued`, then `claim()` move the row to `running`, after which the stale
cancellation overwrote the row to `cancelled` while the claimant still
held its `running` snapshot. Cancellation now reads and writes inside one
`BEGIN IMMEDIATE` transaction: queued jobs are cancelled before claim can
select them, while running jobs get `cancel_requested`. Deletion likewise
checks terminal status and removes the row in one transaction. The
regression interleaving now proves a cancelled row cannot be claimed.
Full suite: 1158 passed, 40 subtests.

After deployment, a production Hammer Session! S01E01 POST completed with
KEEP semantics (`outputs: []`); its `.ja.srt`, `.en.srt`, and `.en.hi.srt`
files matched their pre-run backups byte-for-byte. Backup:
`/cache/verification-backups/20260929-job-cancel-transaction/`.
