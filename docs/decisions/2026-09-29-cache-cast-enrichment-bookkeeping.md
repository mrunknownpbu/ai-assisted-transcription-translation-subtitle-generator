# Cache cast-enrichment bookkeeping (2026-09-29)

Cast enrichment now computes each candidate's surface forms once and
reuses them for subtitle matching, glossary collision checks, and writes.
It builds an entity-form index through one helper and retains candidate
report records by name, avoiding repeated entity-index construction and a
linear report scan when a candidate is skipped during the final merge.
This is primarily a readability improvement; glossary/cast collections
are small, so no material runtime gain is claimed. Cast-enrichment tests:
20 passed, 10 subtests.

A production Hammer Session! S01E01 job completed with KEEP semantics
(`outputs: []`); `.ja.srt`, `.en.srt`, and `.en.hi.srt` remained
byte-identical to backups in
`/cache/verification-backups/20260929-cast-bookkeeping/`.
