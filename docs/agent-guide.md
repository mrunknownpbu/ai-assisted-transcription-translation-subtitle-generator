# Agent guide

The authoritative guide for AI coding agents (and humans) changing Subtitle AI.
`CLAUDE.md` is the short version; this is the working procedure.

## Before modifying code

1. Read `docs/product-requirements.md` and `docs/architecture.md`.
2. Read the decision records that touch your area (`docs/decisions/README.md`).
3. Read `docs/handover.md`: current state, open issues, what the last session
   left half-done. If it disagrees with the code or `git log`, trust the code and
   fix the document.
4. Read the module you will change and its tests. Find the boundary you are
   crossing (architecture, "Components and dependency rules").
5. Check `git log --oneline -30` for what shipped recently. A `docs/*-handoff.md`
   file is an explicitly scoped open item; when its acceptance criteria are met,
   delete or update it.

Do not trust a prior session's summary or a leftover plan over these sources.

## Change protocol

```
UNDERSTAND -> LOCATE -> DESIGN -> IMPLEMENT -> TEST -> DOCUMENT -> VERIFY
```

For any non-trivial change, and always before changing architecture, state:

```
Problem            Current behaviour     Evidence
Proposed design    Alternatives          Trade-offs
Migration impact   Test strategy
```

For a change to existing behaviour: write a characterization test of what it does
now, make the change, then update the test to the intended behaviour in the same
commit. For a bug: reproduce it in a failing test first.

A significant decision gets a record in `docs/decisions/` (format below) and a
row in its `README.md`. Measured claims need the real observation; never present
an assumption as a measured result.

## During implementation

- Preserve the boundaries in `docs/architecture.md`. Do not reach across them to
  make something work.
- No speculative abstractions, no unrelated refactoring, no moving code only to
  shrink a file.
- Add tests with every behaviour change. Never delete or weaken a test to make a
  change pass; if a test encodes behaviour you are deliberately changing, change
  it openly and say why in the commit and the decision record.
- No magic constants without evidence (a measurement, or a cited standard).
- Do not change product behaviour silently; state it in the commit and docs.
- Update documentation when behaviour, the API, configuration or architecture
  changes. `docs/api.md` is checked against the routes by a test.
- Commit in logical units, each with tests passing.

## After implementation

Run what CI runs, using the repository's own commands (`CLAUDE.md`, "Commands"):
ruff, mypy, backend tests, frontend typecheck/tests/build. For a Docker or
runtime change also build the image and exercise the running container
(`docs/deployment.md`). Claim only what you ran and saw.

Before ending a session update `docs/handover.md`: what changed (paths, commit),
every issue noticed but not fixed, and the next recommended tasks.

## Guardrails

- Workflow A takes nothing textual from existing subtitles; Workflow B never runs
  ASR. If a change needs subtitle text in Workflow A, it is a decision for the
  owner, not an implementation detail.
- Do not add protected entities because a name recurs; require the full evidence.
- Do not silently drop malformed remote translation responses or partial output.
- Do not change default GPU behaviour without checking dedicated vs shared GPU.
- Do not hide a failed write, database update or stage behind broad exception
  handling; fail the job with a typed error (`errors.py`).
- Do not write deployment-owned glossary YAML with a lossy writer.
- `.gitignore` (and `.dockerignore`) exclude any path containing `token`, `secret` or `credential`
  (a secrets safeguard). A new file with one of those words in its name is silently never committed;
  local tests pass and CI fails. Check `git status` shows every new file, and verify with a clean
  `git archive HEAD` build when in doubt.
- Do not rewrite historical decision records to look current; add a new one that
  supersedes it.
- Do not add authentication machinery, an ORM, a queue service or a new database
  without a concrete requirement and evidence (`docs/architecture.md`, "Rejected
  directions").

## Specialist scopes

| Scope | Covers | Required checks |
|---|---|---|
| API and jobs | Routes, job lifecycle, validation | API and job-store tests; the every-route-needs-the-key test |
| Pipeline | ASR, translation, segmentation, projection, QC | Pipeline tests; a real-data evaluation for behaviour changes |
| Frontend | Pages, components, hooks, tokens | typecheck, Vitest, build; tokens test |
| Glossary and cast | Protection, mining, metadata | Evidence bar; glossary-file locking tests |
| Reliability | Worker, storage, cleanup, GPU, Docker | Failure-path tests; atomicity and lock tests |
| Documentation | `docs/` accuracy | Verify each claim against code; docs-consistency tests |

## Decision record format

```
# Decision: <title>
Date:
Status: Accepted / Rejected / Superseded
## Context
## Problem
## Evidence          (real observations: counts, files, commands, dates)
## Options considered
## Decision
## Consequences
## Rejected alternatives
## Validation        (the tests and measurements that back it)
```

Dated records are named `YYYY-MM-DD-<slug>.md`. Older records (before 2026-10-02)
use a looser narrative form and are kept as written.
