# Serialize glossary edits and cast-enrichment writes (2026-09-29)

Before the change, a barrier-synchronized reproduction of 20 pairs of
same-series promotions lost at least one update in all 20 runs; 16 runs
also raised from the shared fixed `.yaml.tmp` file. Glossary API
promote/update/delete now hold a directory `flock` across read, mutation,
write, and commit. Cast enrichment re-reads and merges the current series
file under the same lock before writing, preserving manual edits made
during its probe. Glossary and auto-suggestion YAML writes use unique,
same-directory temporary files, fsync, and atomic replace. The equivalent
20-run concurrent promotion check now has zero lost updates and zero
request exceptions. Full suite: 1156 passed, 40 subtests.

After deployment, a production Hammer Session! S01E01 POST completed with
KEEP semantics (`outputs: []`). Its `.ja.srt`, `.en.srt`, `.en.hi.srt`,
cast report, and glossary suggestion YAML matched their pre-run backups
byte-for-byte; no series-specific manual glossary existed before or after
the job. Backups:
`/cache/verification-backups/20260929-glossary-edit-lock-final/`.

**Lock/commit separation measurement (2026-09-29):** A temporary real-Git
benchmark ran 32 concurrent same-series promotions across eight threads.
Total elapsed time was 0.915s; lock wait was p50 79.951ms / p95
259.832ms, lock hold p50 28.125ms / p95 44.561ms, while Git commit
duration was p50 7.449ms / p95 7.910ms (32 runs). The wait is dominated
by queued concurrent edits, not the Git subprocess. Releasing the edit
lock before committing would add index/content races to save only a few
milliseconds per edit, so the current simple serialization is retained;
revisit only if production lock-wait telemetry shows a meaningful user
impact.
