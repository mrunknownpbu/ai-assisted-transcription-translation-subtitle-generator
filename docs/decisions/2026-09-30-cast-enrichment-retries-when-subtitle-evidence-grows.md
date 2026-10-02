# Cast enrichment retries when subtitle evidence grows (2026-09-30)

The 30-day staleness gap for "If You Love" is fixed without adding a broad
polling or API-download loop. Job/report history contained four saved series
reports: only `tvdb-435293` was checked with one completed episode and then
grew to six; the other no-protection reports were Japanese-script
evidence-matching limits rather than missing episodes. The failure mode is
therefore real but narrow.

`cast_enrichment.needs_evidence_refresh()` retains ordinary time staleness,
then adds one cheap branch: a report qualifies only when a skipped candidate
already met `MIN_NAME_LINES` but missed `MIN_NAME_EPISODES`, and the
filename-only count of non-English source subtitle episodes exceeds the
report's saved `episodes_with_subtitles`. `worker._maybe_refresh_cast()`
calls this before enrichment. It does not parse subtitles, call metadata
services, or download IMDb data until both conditions hold; after enrichment,
the saved report advances the count and the next idle tick returns to the
normal 30-day cadence.

Tests cover the positive growth case and the zero-name-evidence control that
must not retry merely because a new subtitle exists. Targeted cast/worker
tests: 96 passed, 10 subtests. Full backend suite: 1217 passed, 48 subtests.
