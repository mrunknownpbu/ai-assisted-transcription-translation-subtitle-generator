# Handover Log

## Purpose

Read this after `README.md` at the start of a session and update it before
ending one when there is work to hand over. It is a short, chronological
ledger, newest first — **not** a replacement for the evidence-based writeups in
`docs/decisions/`. Every entry here should be 1-3 lines and link out to
the full detail (a `docs/decisions/` entry for something shipped, a
`docs/*-handoff.md` file for something open) rather than duplicating it.

**Every agent or session working in this repo should log here:**

1. **Every change you make** that isn't a purely trivial edit (a typo fix,
   a comment). One line, with the affected paths, a commit hash if the work
   was committed, or an `Uncommitted` marker plus the relevant `git status`
   summary otherwise. Include a pointer to the full `CLAUDE.md` entry if one
   exists.
2. **Every issue or gap you identify but do not fix** — whether it was out
   of scope for your task, deliberately deferred, or just noticed in
   passing. If it's substantial enough to need its own acceptance criteria,
   write a `docs/<short-name>-handoff.md` (see the two existing examples
   below for the expected shape) and link it here; if it's small, describe
   it inline.

Do not end a session that changed anything or found anything unresolved
without updating this file. That is the definition of "handover" here.

## Current state (2026-10-02)

- **Product:** two workflows (video to English, subtitle to English) in one container;
  413 jobs processed, queue empty; running container (image built 2026-09-30) predates
  everything below and has not been recreated.
- **Quality gates:** backend 1,358 tests (1 skipped), frontend 98, ruff and mypy with no
  exclusions, CI green on the last verified push; backend line coverage 90%.
- **Documents:** PRD, architecture, design system, agent guide, API, testing, deployment,
  troubleshooting and the decision log are current; `CLAUDE.md` is invariants and commands only.
- **Pushed?** Everything up to `d2b720c` is on `origin/master`; later commits (this rebuild
  work) are local until pushed.

## Completed work (this rebuild, 2026-10-02)

Audit and classification (`decisions/2026-10-02-architecture-audit.md`); typed errors
(`errors.py`) and one `_fail_job()`; configuration snapshot per job; derived `lifecycle`
state; Workflow A no longer takes subtitle text as input (name-correction vocabulary from
cached transcripts; subtitle-mined hotwords removed); API key now required on
`POST /api/srt-translations` with a route-enumerating test; Workflow B no longer imports ASR;
timing evaluator fixed (word anchoring, window medians) with measured real-data evidence;
real-pair timing fixture slot; design tokens with an enforcing test; pipeline-level contract
tests for both workflows; API reference with a route-coverage test; rewritten PRD, architecture,
design system; new agent guide, testing, deployment and troubleshooting docs.

## Known issues

- The reported ~5.1 s human/AI offset is unresolved: the real pair is missing. On four real
  episodes the pipeline's timeline agrees with a human reference within 0.2 s
  (`decisions/subtitle-timing.md`).
- Malay (and any non-Turkish) hallucinations are not suppressed: `hallucination_signatures.json`
  has only Turkish patterns. A Malay film (Pewaris Susuk, 2026) has about 31 lines such as
  "Terima kasih kerana menonton!" and "Sari kata oleh SDI Media" in its `.ms.srt` and 42 matching
  lines in its `.en.srt`. Not addressed (owner declined the fix on 2026-10-02).
- `needs_review` counts hallucination findings that were already suppressed (Love Is In The Air
  S02E05 reports 24 for 24 suppressed lines). Not addressed.
- The worker has no graceful shutdown; a stop kills a running job and the next start re-queues it.
- `skipped` is a stored job status that nothing sets.

## Open architectural questions

- Provider boundary: introduce `TranslationProvider` first (two real implementations) or both
  providers together? Preference: translation first.
- Remove `skipped`, `reference_aligner.py` and/or `turns.py`, or keep? Owner decision.
- Should stored `status` values eventually be renamed to the lifecycle names (a data migration and
  client change), or does the derived `lifecycle` field suffice?
- CI builds only a Dockerfile lint (`docker build --check`) and the frontend stage; the full 10 GB
  CUDA image is not built on a hosted runner. Acceptable, or use a self-hosted runner?

## Pending experiments

- Name-correction vocabulary on a series with few cached transcripts (the veto weakens; not
  measured on a cold series).
- The 0.001 s/s drift limit in the evaluator is unvalidated (needs a frame-rate-mismatched pair).
- Performance: real-time factor per stage (PRD marks it TBD).

## Validation performed (2026-10-02, image `subtitle-ai:validate`, a side container on port 18099 with a scratch media folder; the live container was not touched)

| Check | Result |
|---|---|
| Image build | Succeeded (4 min cold, about 10.5 GB). |
| Startup guard | No key on `0.0.0.0`: the process exits with the "refusing to start" error. With a key: healthy in 20 s. |
| Health | `ok`, `status: ok`, `db_ok`, `worker_alive` present. |
| API | 401 without the key, including on `POST /api/srt-translations`; 200 with it; unknown `/api/*` path is JSON 404. |
| Frontend | `/` and `/assets/*` served (200). |
| GPU | `torch.cuda.is_available()` True (RTX 3070); during a job GPU memory rose from 187 to 4,343 MiB at 97% utilisation. |
| Workflow A | A 150 s clip of Love Is In The Air S01E01 completed in 25 s: Turkish detected (0.98), `.tr.srt` and `.en.srt` valid, `config_snapshot` present, glossary name "Eda" preserved. |
| Workflow B | A 32-cue Turkish upload completed in 0.7 s: no audio or ASR events, original saved beside the English, every English cue inside its source cue and start/end times identical to the source. |
| Batch | Two queued video jobs ran one at a time and both succeeded; a duplicate of an active job was refused (409). |
| Cancellation | A queued job cancelled immediately; a running job went `CANCELLING` then `CANCELLED` and the existing English subtitle was byte-identical afterwards. |
| Failure and retry | A non-video file failed as `MEDIA_ERROR` with a hint; retry created a new job (attempt 2, `retry_of` set) and left the original untouched. |
| Atomic output failure | A read-only media folder: the existing subtitle was byte-identical and no temp files remained. This first failed as `PIPELINE_ERROR` with a raw errno; fixed to `OUTPUT_ERROR` with a hint and re-verified in a rebuilt image. |
| Logs | With `SUBTITLE_AI_LOG_FORMAT=json`, 24 of 25 application log lines carried the job ID (the other is startup); no tracebacks. |
| Rescan | Not testable: Plex and Jellyfin are not configured in the side container. |

## Required validation (still open)

- Recreate the LIVE container from a fresh image (`./scripts/deploy.sh`) and repeat the health, API
  and frontend checks. It was not recreated: `.env` has neither `SUBTITLE_AI_API_KEY` nor
  `SUBTITLE_AI_ALLOW_INSECURE=1`, so the new image would refuse to start. That is the owner's call.
- A real rescan against Plex or Jellyfin.
- Re-run Workflow A on the Malay film to see the unsuppressed hallucinations again after any signature work.
- Re-check CI after pushing.

## Known technical debt

`api.py`, `worker.py`, `translate.py` size and coupling; module-level environment reads; jobs as
dicts; `TranslateSrtPage.tsx` (578 lines); no provider interfaces; tests that patch internal seams
by name; frontend coverage unmeasured; no automated end-to-end or Docker test.

## Next recommended implementation tasks

1. Push the local commits and confirm CI.
2. Extract the sampler and report helpers from `api.py`; split it into routers.
3. Split `translate.py`; add `TranslationProvider` (local and remote) with fakes in tests.
4. Pass an explicit configuration object into the pipelines.
5. Graceful worker stop with a checkpoint.
6. Add the real timing pair when available (`tests/fixtures/timing/README.md`).
7. Hallucination signatures for other languages, with evidence, if the owner wants them.

---

# Ledger

The sections below are the original append-only record (open issues and change log). They are
history; the sections above are the summary.

## How to log an entry

**A change you made:**
```
- YYYY-MM-DD — <what changed and why, one line>. Paths:
  `<path>`, `<path>`. Commit `<hash>` / Uncommitted (`git status`:
  `<summary>`).
  Full detail: CLAUDE.md ("<section title>").
```

**An issue you found but didn't fix:**
```
- YYYY-MM-DD — OPEN: <the problem, one line>. <link to
  docs/<name>-handoff.md, or enough detail to act on inline if it's small>.
```

**Resolving a previously logged open issue:** don't delete or rewrite the
original line — append a resolution note under it:
```
- YYYY-MM-DD — OPEN: ...
  - RESOLVED YYYY-MM-DD, commit `<hash>` / Uncommitted (`git status`:
    `<summary>`), see CLAUDE.md ("<section>").
```

Never edit or delete another session's entry other than to append a
RESOLVED note to one of its OPEN lines. This file is additive history, the
same as `CLAUDE.md`.

## Open issues (ledger)

- 2026-10-01 — OPEN: the reported ~5.1-second human/AI SRT offset could not
  be reproduced because no matching reference/candidate pair is in this
  checkout. Run `scripts/eval_srt_quality.py reference.srt candidate.srt`
  against the actual pair before changing media timestamp origin; it reports
  constant offset separately from linear/non-linear drift.
- 2026-09-30 — OPEN: setting `SUBTITLE_AI_API_KEY` now protects all API
  reads and SSE, but the bundled browser UI has no secure API-key provisioning
  mechanism; deploy it behind same-origin reverse-proxy authentication or add
  an explicit authenticated browser-session design before enabling the key for
  direct UI access.
  - RESOLVED 2026-09-30, Uncommitted (`git status`: authenticated event-stream
    client, frontend regression test, and handover modified). The browser
    already prompts for and stores the key for ordinary API requests; live
    updates now use authenticated `fetch()` SSE instead of headerless
    `EventSource`, so they send the same `X-API-Key` without exposing it in a
    URL. Frontend typecheck and 94 tests passed.
- 2026-09-30 — OPEN: protected character names in `glossary.py`'s
  `protect()` don't match when a Turkish case suffix is glued directly onto
  the name with no apostrophe (`Ateşi`, `Ateşin` still translate as "Fire"
  even though bare `Ateş` is protected). See
  `docs/glossary-suffix-protection-handoff.md`.
  - RESOLVED 2026-09-30, Uncommitted (`git status`: glossary matcher,
    profile loader, glossary tests, `tvdb-435293.yaml`, and documentation
    modified). See CLAUDE.md ("Turkish glued case-suffix protection").
- 2026-09-30 — OPEN: `cast_enrichment.py`'s per-series staleness interval
  (flat 30 days) doesn't react to new episodes finishing transcription, so
  a series that failed the evidence gate on its first check (too few
  episodes yet) can stay unprotected for a month even after enough episodes
  exist. See `docs/cast-enrichment-staleness-handoff.md`.
  - RESOLVED 2026-09-30, Uncommitted (`git status`: cast enrichment,
    worker, cast tests, and documentation modified). See CLAUDE.md
    ("Cast enrichment retries when subtitle evidence grows").
- 2026-09-29 — OPEN, lower priority, not yet scoped: short Spanish clauses
  in "If You Love" (2023) S01E01's cold open (e.g. "Senor, si, kien es?")
  are too short to reach the 3-clause code-switch corroboration threshold
  in `langid.py` and still get translated using the Turkish tokenizer.
  Disclosed, accepted limitation — see CLAUDE.md's "Code-switched dialogue
  mistranslated" entry for why `min_run=3` is correct and shouldn't be
  lowered without new evidence. No handoff doc yet; would need its own
  measurement pass if picked up.

## Change log

- 2026-10-02 — Regression fixture for the ~5.1 s human/AI offset, and a fix to
  the evaluator it exposed: `scripts/eval_srt_quality.py` paired cues by time
  overlap, which pairs every cue with the wrong neighbour once the shift
  exceeds a cue's length and the two files are segmented differently (a 5.1 s
  shift read as a 2.5 s mean and "drift"). It now aligns on shared word runs
  (>= 3 words), falling back to the old cue pairing only when none exist, and
  reports `pairing: words|cues`. `tests/srt_offset_fixtures.py` builds a
  synthetic reference and shifted / re-segmented / stepped / drifting
  candidates with a known offset; `tests/test_eval_srt_quality.py` pins the
  verdicts (5 of the 7 new tests fail on the old evaluator). The original
  OPEN item below still stands: the real pair is still needed to say whether
  the production pipeline has an offset. Suite: 1297 passed.

- 2026-10-02 — Cleared the mypy baseline: all 12 excluded modules now pass and the
  override list is gone from `pyproject.toml`. Fixes are annotations, `Optional`
  narrowing, and `assert x is not None` where an invariant already held (those
  turn a would-be `TypeError` into an `AssertionError`, nothing else). Behaviour
  changes, all small: `POST /api/jobs/{id}/cancel` returns 404 instead of a 500
  if the job vanishes mid-cancel; the idle movie cast refresh skips a Radarr
  movie with no path instead of logging a failed attempt; a glossary file that
  parses to nothing no longer raises when read for its title. One
  `# type: ignore[attr-defined]` set in `events.py` for asyncio.Queue internals.
  Paths: `subtitle_ai/{api,worker,jobstore,translate,pipeline,cast_enrichment,
  cast_metadata,glossary_profile,events,reference_aligner,tvdb_client}.py`,
  `pyproject.toml`, `CLAUDE.md`. Suite: 1290 passed; ruff and mypy clean.

- 2026-10-02 — `/api/health` now checks the database and worker thread (503 if
  either is down; `status`/`db_ok`/`worker_alive`/`reasons` fields added), and
  logging moved to `logging_setup.py`: job id on every line logged during a
  job, optional JSON output (`SUBTITLE_AI_LOG_FORMAT=json`), `SUBTITLE_AI_LOG_LEVEL`.
  Paths: `subtitle_ai/{logging_setup.py,api.py,worker.py,jobstore.py,main.py}`,
  `compose.yml`, `.env.example`, `README.md`, `tests/test_{logging_setup,api}.py`.
  Not done: metrics (Prometheus) and logging from pipeline/ASR stages beyond
  the job-id tag. Suite: 1290 passed.

- 2026-10-02 — Added ruff (correctness rules: E4/E7/E9/F) and mypy to CI, config
  in `pyproject.toml`; removed 22 unused imports. mypy excludes 12 modules with
  68 existing errors (list in `[[tool.mypy.overrides]]`) -- OPEN: burn that
  list down. Paths: `.github/workflows/test.yml`, `pyproject.toml`,
  `CLAUDE.md`, plus unused-import removals in `subtitle_ai/{pipeline,translate}.py`,
  `scripts/compare_turn_detectors.py` and several tests. Suite: 1281 passed.

- 2026-10-02 — Server now refuses to start on a non-loopback address with no
  `SUBTITLE_AI_API_KEY` unless `SUBTITLE_AI_ALLOW_INSECURE=1`. **A deployment
  with neither (the current `.env`) will exit on next restart.** Paths:
  `subtitle_ai/{startup_guard.py,main.py}`, `compose.yml`, `.env.example`,
  `README.md`, `tests/test_startup_guard.py`. Full detail:
  `docs/decisions/2026-10-02-refuse-open-api-beyond-loopback.md`.

- 2026-10-01 — Added conservative final-cue re-segmentation and centralized
  subtitle constraints: incomplete sub-0.5s fragments merge only across a
  short non-turn/non-pause boundary; target pieces merge when their audio
  envelope cannot support the minimum duration. Paths:
  `subtitle_ai/{subtitle_constraints.py,segmentation_source.py,segmentation_target.py,qc/readability_qc.py}`,
  `.env.example`, `compose.yml`; uncommitted alongside pre-existing work.
  Added `scripts/eval_srt_quality.py` plus regression tests for fragment
  merging and constant-offset versus drift detection. Full suite: 1269 passed,
  3 warnings, 71 subtests.
- 2026-09-30 — Reran audio transcription and English translation for Love Is
  In The Air (2020) S02E01–E03 and Queen of Tears (2025) S01E01–E03; all six
  jobs completed. Saved each old `.tr.srt`/`.en.srt` as
  `.pre-rerun-20260930T1701.srt`; regenerated files parsed successfully.
- 2026-09-30 — Refined uncovered-gap evidence so nearby source timestamps no
  longer claim proven timing drift. Hammer Session! S01E01 now classifies 8
  vocalizations (18.7s), 4 brief reactions (8.0s), 49 nearby-boundary
  candidates (120.3s), 18 isolated ASR gaps (46.1s), 4 between-cue gaps
  (12.6s), and 19 non-dialogue references (69.5s). Paths: human-reference
  evaluator, its tests, and handover. Uncommitted (`git status`: measurement
  scripts, tests, and handover). Focused tests: 18 passed.
- 2026-09-30 — Redeployed the current workspace and ran the new read-only
  analyzers against production-library files. Hammer Session! S01E01:
  102/756 (13.5%) human cues uncovered — timing drift 58 (140.4s), isolated
  ASR gaps 21 (52.7s), between-cue gaps 4 (12.6s), non-dialogue 19 (69.5s);
  Love Is In The Air S01E02 produced zero two-clause code-switch candidates.
  Analyzer scripts are intentionally copied into the container ad hoc (the
  image follows the existing evaluation-script convention and does not ship
  `scripts/`). Service health passed with a current worker heartbeat.
  Uncommitted (`git status`: measurement scripts, tests, and handover).
- 2026-09-30 — Added four measurement-only analysis tools: proper-name ASR
  recall, hand-annotated speaker-turn scoring, pre-generated
  translation-context candidate comparison, and transparent QC review-priority
  ranking. Paths: `scripts/analyze_proper_name_recall.py`,
  `scripts/eval_turn_ground_truth.py`,
  `scripts/eval_translation_context_candidates.py`,
  `scripts/analyze_qc_review_priority.py`, and matching tests. Uncommitted
  (`git status`: measurement scripts, tests, and handover modified). Combined
  focused tests: 18 passed; no production behavior changed.
- 2026-09-30 — Added `scripts/analyze_short_code_switches.py` to report
  review-only short foreign-language candidate runs and repeated-text
  false-positive risk from source SRTs; production code-switch thresholds are
  unchanged. Paths: analyzer and `tests/test_analyze_short_code_switches.py`.
  Uncommitted (`git status`: ASR/code-switch analysis, tests, and handover
  modified). Focused tests: 17 passed.
- 2026-09-30 — Added observable uncovered-ASR-gap classification to
  `scripts/eval_against_human_en_reference.py`: timing drift, between-cue
  gap, isolated ASR gap, and non-dialogue reference cues, with JSON summary
  output. It deliberately does not infer hallucination/root cause from timing
  alone. Paths: evaluator and `tests/test_eval_against_human_en_reference.py`.
  Uncommitted (`git status`: ASR-gap analysis, tests, and handover modified).
  Focused tests: 16 passed.
- 2026-09-30 — Committed and pushed the reliability, API-key, telemetry,
  CI, and authenticated-SSE changes as `344b56f`
  (`feat: harden job reliability and live updates`), then rebuilt and
  redeployed local `subtitle-ai`. `GET /api/health` passed with a current
  worker heartbeat. Uncommitted (`git status`: this handover update only).
- 2026-09-30 — Replaced headerless `EventSource` live updates with an
  authenticated `fetch()` SSE reader so browser API-key mode also receives
  job-change signals. Paths: `frontend/src/api/client.ts`,
  `frontend/src/api/useEventStream.ts`, and
  `frontend/src/api/useEventStream.test.tsx`. Uncommitted (`git status`:
  frontend event client, test, and handover modified). Frontend typecheck and
  94 tests passed.
- 2026-09-30 — Hardened SSE/API reliability and job durability: bounded,
  coalescing event queues with heartbeats/connection cap; API-key read
  protection; capped job logs; finite orphan recovery; phase-throughput ETA;
  locked CPU-only CI dependencies and worker smoke coverage. Paths:
  `subtitle_ai/events.py`, `subtitle_ai/api.py`, `subtitle_ai/jobstore.py`,
  `subtitle_ai/worker.py`, `frontend/src/{api/types.ts,components/JobTable.tsx,components/ProgressBar.tsx,pages/JobDetailPage.tsx}`,
  `.github/workflows/test.yml`, `README.md`, and related tests. Uncommitted
  (`git status`: reliability/API/worker/frontend/CI/docs/tests modified).
  Full validation: 1229 backend tests, 48 subtests; frontend typecheck, 93
  tests, and production build passed.
- 2026-09-30 — Changed the default cast-metadata refresh interval from 30
  days to 12 hours (`0.5` days), including the Series UI, environment
  template, and README. Paths: `subtitle_ai/cast_enrichment.py`,
  `frontend/src/components/CastReportPanel.tsx`,
  `frontend/src/components/CastReportPanel.test.tsx`,
  `tests/test_cast_enrichment.py`, `.env.example`, `README.md`, `CLAUDE.md`.
  Uncommitted (`git status`: refresh configuration, tests, and docs modified).
  Focused tests: 22 backend and 3 frontend passed.
- 2026-09-30 — Added a bounded early cast-enrichment retry when a prior
  report missed only the multi-episode evidence gate and source-subtitle
  episode count grows. Paths: `subtitle_ai/cast_enrichment.py`,
  `subtitle_ai/worker.py`, `tests/test_cast_enrichment.py`,
  `docs/cast-enrichment-staleness-handoff.md`, `CLAUDE.md`. Uncommitted
  (`git status`: cast refresh implementation, tests, and docs modified).
  Targeted tests: 96 passed, 10 subtests.
- 2026-09-30 — Added explicit, vowel-harmonized Turkish case-suffix
  protection for opted-in entities and enabled it for `Ateş`; measured 14
  expected source changes and no unrelated changes across 49 Turkish SRTs.
  Paths: `subtitle_ai/glossary.py`, `subtitle_ai/glossary_profile.py`,
  `tests/test_glossary.py`, `tests/test_glossary_profile.py`,
  `docs/glossary-suffix-protection-handoff.md`, `CLAUDE.md`; glossary repo:
  `tvdb-435293.yaml`. Uncommitted (`git status`: matcher, tests, glossary
  data, and docs modified). Full backend suite: 1215 passed, 48 subtests.
- 2026-09-30 — Aligned session-start guidance with the existing README-first
  project convention and updated handover templates to support uncommitted
  work. Paths: `docs/handover.md`, `docs/agents.md`. Uncommitted (`git
  status`: modified handover and agent guides).
- 2026-09-30 — Re-ran `cast_enrichment.enrich_series()` for "If You Love"
  (2023) (tvdb-435293) now that all 6 episodes have transcripts; protected
  10 real credited character names (`Ateş`, `Leyla`, `Füsun`, `İlter`,
  `Yakup`, `Meryem`, `Umut`, `Onur`, `Barış`, `Bige`) that were previously
  translated as ordinary Turkish words. Regenerated all 6 episodes'
  `.tr.srt`/`.en.srt` and verified the fix in the real output (backups kept
  before regenerating). Config-only change (`glossary/tvdb-435293.yaml`),
  no code touched. Full detail: CLAUDE.md ("'If You Love' (2023): lead
  character's name translated as 'Fire'").
- 2026-09-30 — Added `docs/product-requirements.md`, `docs/architecture.md`,
  `docs/design-system.md`, `docs/agents.md` accuracy pass: filled in 6
  backend modules missing from `architecture.md`'s component table
  (`auto_glossary.py`, `langid.py`, `turns.py`, `hallucination.py`,
  `name_correction.py`, split `glossary.py`/`glossary_profile.py`), added a
  "Feature toggles" note (code existing != shipped-on-by-default), and gave
  all four docs a consistent "See also"/handover pointer chain. Commit
  `39dea24` covers the earlier CLAUDE.md + handoff-doc portion of this
  session; this doc pass and this file were not yet committed as of writing
  this entry.
- 2026-09-30 — Drafted `docs/glossary-suffix-protection-handoff.md` and
  `docs/cast-enrichment-staleness-handoff.md` (Copilot-coding-agent-ready
  issue format) for the two residual gaps found while verifying the "If You
  Love" fix above. Not posted as GitHub issues yet — drafts only. Commit
  `39dea24`.
