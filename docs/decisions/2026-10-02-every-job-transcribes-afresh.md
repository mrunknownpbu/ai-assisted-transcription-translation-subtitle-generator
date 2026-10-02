# Decision: every job transcribes the audio afresh

Date: 2026-10-02

Status: Accepted

## Context

Until now a video job reused a cached transcript when one existed for the same
media, audio stream, model, settings and pipeline version; only a retry bypassed
the cache. The owner asked that every job run fresh transcription.

## Problem

Reuse made a re-run fast (9 s against 116 s for one episode) but meant a job's
subtitle could reflect an earlier run's decoding rather than the current models and
settings, and made "run it again" silently a no-op for transcription.

## Evidence

2026-10-02, Happy Kanako's Killer Life S02E04 on the live container: the cached
run took 9 s with 192 transcription QC findings; the fresh retry took 116 s
(102 s transcribing, GPU peak 5,229 MiB at 99%) with 147 findings and the same
language (Japanese, 0.68). Whisper decoding is therefore not bit-repeatable between
runs on this setup, so a cached transcript is not interchangeable with a fresh one.

A first attempt at this change was only a retry: a retry passed no cache directory,
so its fresh transcript was **not stored** and name correction found no series
vocabulary for it (the vocabulary is read from the cache). Both were defects of that
approach.

## Options considered

1. Pass no cache directory to every job (the retry approach): fresh, but nothing is
   stored and name correction loses its vocabulary.
2. Always transcribe afresh, still write every transcript to the cache, make reuse an
   opt-in setting (chosen).
3. Delete the cache: loses the vocabulary source and the audit trail.

## Decision

`SUBTITLE_AI_REUSE_TRANSCRIPT_CACHE` (default off). The worker always passes the cache
directory; `pipeline.run(reuse_cached_transcript=...)` skips the lookup unless the
setting is on, and a retry never reuses. Every fresh transcript is written (replacing
the entry with the same key), so the cache always holds the latest transcript per
episode and name correction's series vocabulary stays populated. The setting is recorded
in each job's `config_snapshot`.

## Consequences

- Every job pays the full transcription time (about 1-3.5 minutes for the episodes seen
  here on the RTX 3070), where a re-run was seconds.
- A re-run can produce a slightly different subtitle from the last one for the same
  media. That is expected, not a regression.
- Setting `SUBTITLE_AI_REUSE_TRANSCRIPT_CACHE=on` restores reuse, for example for batch
  re-translation after a glossary change.
- The cache grows by one entry per distinct media/settings key, not per job.

## Rejected alternatives

Options 1 and 3 above.

## Validation

`tests/test_pipeline_cache.py` (reuse off transcribes again despite a hit and replaces the
entry; reuse on still hits), `tests/test_worker.py` `FreshTranscriptionTests` (default
fresh with the cache still written, opt-in reuse, retry never reuses). Live check after
redeploy is recorded in `docs/handover.md`.
