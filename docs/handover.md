# Handover Log

## Purpose

Read this after `README.md` at the start of a session and update it before
ending one when there is work to hand over. It is a short, chronological
ledger, newest first — **not** a replacement for `CLAUDE.md`'s detailed,
evidence-based writeups. Every entry here should be 1-3 lines and link out to
the full detail (a `CLAUDE.md` section for something shipped, a
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

## Open issues

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
