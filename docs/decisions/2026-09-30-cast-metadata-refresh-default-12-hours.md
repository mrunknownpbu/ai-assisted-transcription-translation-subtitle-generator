# Cast metadata refresh default: 12 hours (2026-09-30)

`SUBTITLE_AI_CAST_REFRESH_DAYS` now defaults to `0.5` (12 hours), rather
than 30 days. This makes ordinary cast-metadata discovery more responsive
while retaining idle-only, one-series-per-tick execution; `0` still disables
automatic checks. The unchecked-Series UI, README, and `.env.example` state
the same default. Tests: 22 cast-enrichment tests and 3 CastReportPanel
frontend tests passed.
