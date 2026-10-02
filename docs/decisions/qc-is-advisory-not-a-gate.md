# QC is advisory, not a gate -- on purpose

*Standing policy, moved verbatim from `CLAUDE.md` (2026-10-02). Date of the underlying measurements: 2026-09-28.*


`pipeline.py`/`srt_translation.py`'s `valid` computation only fails a
job on segmentation/timing/output-structural QC findings.
Translation/entity/hallucination/readability findings never block
completion -- real, measured false-positive rates (harmless
interjection-collision "substitution" matches, embellishment-but-not-
wrong translation_error cases) were too high to safely auto-fail on.
Instead, `JobQc.needs_review_count()` (`qc/types.py`) surfaces only
high-confidence findings (entity_error/hallucination categories, or
anything >=0.7 confidence) as a `needs_review` count on the job record,
shown ahead of the generic QC summary in the job list GUI. If you're
tempted to make QC block completion, check the false-positive rate on
real data first -- it's higher than it looks from reading the heuristics
alone.

The same discipline applies in the other direction: any rule emitted at
>=0.7 feeds `needs_review` on every job. Until 2026-09-28 readability's
min-duration rule sat at exactly 0.7 and was 99.97% of all
`needs_review` hits in production (62,490 of 62,512), burying the ~21
real entity/hallucination findings; it and max-duration now emit at
`readability_qc.DURATION_CONFIDENCE` (0.5) -- timing isn't fixable in the
text-only editor, and Workflow B inherits it from the source SRT anyway.
Stored rows were recounted with `scripts/recompute_needs_review.py`
(20,058 -> 22). Before raising any rule to >=0.7, count how often it
fires across the real job database first.
