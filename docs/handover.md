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
