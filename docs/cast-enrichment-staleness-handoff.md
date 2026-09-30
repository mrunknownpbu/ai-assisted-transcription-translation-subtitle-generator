## Problem

`subtitle_ai/cast_enrichment.py` automatically protects real character names (sourced from IMDb/TMDB/TVDB/nfo credits) against literal mistranslation, but only once there's enough real transcript evidence: `MIN_NAME_LINES = 5` and `name_episodes >= 2` (see `_enrich()`, `subtitle_ai/cast_enrichment.py:224-267`). The next check for a series is gated purely by a flat time interval — `cast_enrichment.is_stale()` (`subtitle_ai/cast_enrichment.py:395-401`) is just `now - checked_at > refresh_days() * 86400`, `refresh_days()` defaulting to 30 — regardless of *why* the previous check didn't protect anything.

## Real incident

"If You Love" (2023)'s cast check ran when only 1 of its 6 episodes had a transcript. It correctly found insufficient evidence (`name_episodes < 2`) and skipped protecting anything — including the lead character "Ateş", whose unprotected name was then translated as the literal Turkish word for "fire" throughout every episode ("Mr. Fire", "fire archer", etc.). The other 5 episodes finished transcribing later the same day, but the next automatic cast-enrichment check wasn't due for a full 30 days. The bug would have silently persisted for a month until manually re-triggered (which is what actually happened — a human noticed and forced `cast_enrichment.enrich_series()` early; see `CLAUDE.md`'s dated entry "'If You Love' (2023): lead character's name translated as 'Fire'" for the full writeup).

`subtitle_ai/worker.py`'s `_maybe_refresh_cast()` (`subtitle_ai/worker.py:358-394`) already has a *different* early-retry mechanism, but only for actual errors:

```python
except Exception as exc:
    ...
    retry_at = now - max(0.0, cast_enrichment.refresh_days() - 1) * 86400
```

There is no equivalent for "the evidence gate simply wasn't met yet, but new episodes may have since arrived."

## Constraints

- Must not turn into hammering the IMDb dataset download or TMDB/TVDB APIs on every idle tick for every series. `_maybe_refresh_cast()`'s own docstring notes the IMDb dataset download is the expensive part ("~15s-2min, most of it the IMDb dataset download once per 30 days"). Any fix needs to keep staleness *checks* cheap and only make re-enrichment itself expensive when it actually runs.
- This repo's convention (see `CLAUDE.md`) is measure-first: validate the real scope of the problem (how often does this pattern actually occur across real job history?) before picking a fix, and surface genuine tradeoffs rather than guessing.

## Suggested approach (weigh with real data, don't assume)

1. Check how often a series' first cast check lands when only 1 (or few) episodes are transcribed, across real job history (`GET /api/jobs` on the running instance, or the jobstore directly) — determines whether this is a common failure mode or a one-off.
2. Consider candidate fixes:
   - Re-check when `episodes_with_subtitles` (already recorded in the saved report, e.g. `cache/cast/report-<tvdb_id>.json`) has grown since the last check, even if the time-based staleness gate hasn't cleared.
   - A shorter re-check interval specifically for a series whose last check ended in "skip due to insufficient evidence," versus one that already protected everything or found nothing credit-worthy.
   - Trigger a re-check directly from job completion (the worker already knows exactly when a new episode's translation finishes) instead of relying solely on the idle-tick sweep.
3. Implement whichever approach the measurement in step 1 actually supports.
4. Add/update tests in `tests/test_cast_enrichment.py` and `tests/test_worker.py`.
5. Document the change and its evidence in `CLAUDE.md`.

If the right tradeoff isn't clear from measurement (e.g. checking more often meaningfully increases API/download cost for a benefit that's hard to quantify), leave a clearly flagged open question in the PR description rather than guessing.

## Acceptance criteria

- [ ] A series whose evidence gate wasn't met on its first check gets re-checked automatically once enough new episode data exists, without waiting the full 30-day interval.
- [ ] No increase in IMDb dataset downloads / external API calls for series that are NOT in this "insufficient evidence, more data since arrived" state.
- [ ] Full test suite passes with no regressions.
- [ ] `CLAUDE.md` updated with a dated entry describing the problem, the chosen fix (and why alternatives were rejected, if measurement ruled them out), and verification evidence.

## Relevant files

- `subtitle_ai/cast_enrichment.py` — `_enrich()`, `is_stale()`, `refresh_days()`, `enrich_series()`
- `subtitle_ai/worker.py` — `_maybe_refresh_cast()`, `_maybe_refresh_movie_cast()`
- `tests/test_cast_enrichment.py`, `tests/test_worker.py` — existing test structure to extend
