# Decision: Architecture audit and rebuild strategy

Date: 2026-10-02

Status: Accepted

## Context

A request to rebuild Subtitle AI around explicit product and architectural
contracts, without losing proven behaviour. The first phase is a forensic audit:
read the whole repository, classify what exists, verify the invariants, and only
then change code.

## Problem

Decide what to keep, what to restructure and what to remove; find where the code
violates the stated invariants; and choose a migration order that keeps the 1,297
(now 1,357) backend tests meaningful.

## Evidence

Read: `CLAUDE.md` and the decision log, all of `docs/`, the backend (about 12.7k
lines in 51 modules), the frontend (about 4.7k lines), the tests, the QC package,
Dockerfile, compose files, CI, the SQLite schema and migration, all 24 routes, the
worker, GPU lock, translate server, integrations, SRT parsing and writing, logging,
SSE and the frontend state. Measured: module import graph (no import cycles); backend
line coverage 89% before this work, 90% after; the live job history through the API
(413 jobs, queue empty) and the transcript cache; ffprobe start times of the 36
videos processed.

Verified current-state facts: 413 jobs processed; queue empty; ruff and mypy run with
no exclusions; CI was green on the last push; `CLAUDE.md` was slimmed and history moved
to `docs/`; the startup guard exists; health, job-tagged logging and the mypy baseline
work are in; a fixture and evaluator fix exist for the ~5.1 s offset; the running
container (image `894da8b0e878`, built 2026-09-30) predates the changes; the real offset
pair is not in the checkout.

## Classification

| Area | Class | Why |
|---|---|---|
| Job store: atomic claim/finish/cancel/delete, recovery, additive migration | Preserve | Correct under concurrency, well tested (97% covered). |
| GPU lock, VRAM pre-flight, residency, release | Preserve | Born from real OOMs; each rule cites its incident. |
| Atomic output, path containment | Preserve | Tested; no known defect. |
| Glossary protection, layered profiles, locked YAML writes, evidence-gated cast enrichment | Preserve | Measured and documented policy. |
| QC package (advisory) | Preserve | Policy decision with measured false-positive rates. |
| Hallucination registry, ASR cache provenance, transcript model | Preserve | Typed, versioned, tested. |
| Source/target segmentation, projection, constraints | Preserve | Measured; recently consolidated into one constraints module. |
| SSE bus (coalescing, bounded) | Preserve | Contract is a change signal only; simple and tested. |
| Startup guard, API-key and Origin checks | Preserve | Security boundary. |
| `api.py` (about 1,070 lines, 24 routes) | Refactor | Mixes routes, sampler/model state, report building and pipeline-adjacent imports; split by capability behind the existing tests. |
| `worker.py` (about 850 lines) | Refactor | Both workflows' orchestration plus idle maintenance; imports `api` for the sampler. |
| `translate.py` (about 1,100 lines) | Refactor | Engine, batching, protection and recovery in one file; the provider boundary belongs here. |
| Behaviour toggles read from `os.environ` inside stage modules | Refactor | Implicit global state; snapshot added, explicit config object planned. |
| Jobs as plain dicts | Refactor | Introduce a typed record when a boundary needs it. |
| Error handling: duplicated except-chains in the worker | Refactor (done) | Replaced with typed errors and one `_fail_job()`. |
| Frontend pages (`TranslateSrtPage.tsx` 578 lines) | Refactor | Page-level logic that should be feature modules; not started. |
| Frontend colour values | Refactor (done) | Tokens file plus a test. |
| Name-correction vocabulary from subtitle files; subtitle-mined hotwords | Replace (done) | Violated "audio is the only source"; now cached ASR transcripts and curated glossary only. |
| Unauthenticated `POST /api/srt-translations` | Replace (done) | A missing dependency; a test now walks every route. |
| `skipped` job status | Remove candidate | Defined in the schema and tests, never set by any code. Needs a check that no external consumer relies on it. |
| `reference_aligner.py` (439 lines) | Unknown | Called only by its own tests: QA tooling for human-vs-generated comparison, relevant to the timing investigation. Keep until the owner decides. |
| `turns.py` speaker-turn detection | Unknown | Implemented, measured, shipped off by default; imported by the pipeline. Value unproven. |
| `docs/IMPROVEMENT_PLAN.md`, `ENHANCEMENT_DRAFT.md`, `enhancement.md` | Preserve as history | Frozen planning rounds; not live documents. |

## Findings against the invariants

1. **Audio is the only source (violated, fixed).** `name_correction.series_vocabulary()`
   read sibling `.srt` files to gate edits to ASR output, and `Worker._load_auto_hotwords`
   passed subtitle-mined names to ASR hotwords (opt-in). See
   `2026-10-02-audio-only-asr-inputs.md`.
2. **Security (violated, fixed).** One route lacked the API-key dependency. See
   `2026-10-02-srt-translations-endpoint-was-unauthenticated.md`.
3. **Workflow isolation (weak, fixed).** Workflow B imported `pipeline` (and so ASR) only
   for two exception classes; they now live in `errors.py` and a test pins it.
4. **Dependency direction (violated, open).** `api.py` imports pipeline-side modules and holds
   process state; `worker.py` imports `api`.
5. **Provider isolation (absent, open).** No ASR or translation interface.
6. **Graceful shutdown (absent, open).** The worker is a daemon thread; a stop kills a running
   job and the next start recovers it.
7. **Test gaps (fixed).** `srt_translation.py` pipeline function had no direct tests (54%
   coverage).
8. **Timing diagnostics (defective, fixed).** The evaluator mis-paired cues and over-called
   drift. See `subtitle-timing.md`.
9. **Docker build in CI (absent).** The image is about 10 GB; building it on a hosted runner
   was not attempted.

## Options considered

1. Rewrite the backend around new packages in one pass.
2. Introduce interfaces and typed boundaries incrementally behind characterization tests
   (chosen).
3. Leave structure alone and only document it.

## Decision

Option 2, in the order: document and verify (this audit), fix invariant violations
and security defects, add missing boundary tests, add typed errors and a configuration
snapshot, then restructure `api.py`/`worker.py`/`translate.py` and add provider
interfaces. No big-bang rewrite. Items 4, 5 and 6 above are the remaining structural work
and are listed in `docs/handover.md`.

## Consequences

The rewrite the request describes is only partly done: the contracts, invariants, errors,
snapshot, tokens and tests are in place; the large refactors are not, and are recorded as
such rather than claimed.

## Rejected alternatives

Option 1: a rewrite would discard working, measured behaviour (the code carries many
incident-driven fixes) and cannot be validated without GPU runs on real media. Option 3:
leaves two real invariant violations in place.

## Validation

Each fix above has tests that fail on the old behaviour; the full suite, ruff and mypy pass;
the frontend typechecks, tests and builds.
