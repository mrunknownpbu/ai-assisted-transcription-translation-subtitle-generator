# Agent Guide

## Purpose

This guide defines safe, effective responsibilities for coding agents working
in Subtitle AI. It's one of four durable-intent documents in `docs/`
(`product-requirements.md`, `architecture.md`, `design-system.md`, this file)
that describe what the system is and should do; `README.md` is the
deployment- and API-facing reference (it has its own, overlapping
"Architecture" section — where the two disagree on a structural detail,
prefer `docs/architecture.md` and fix the drift); `CLAUDE.md` is the one place
that records what actually happened — operational constraints, real-data
measurements, and dated historical decisions — and wins over all of the above
when something has changed since they were last updated.

## Picking up where another session left off

Start every session by reading **`docs/handover.md`** — a short, chronological
change/issue log that a prior session is required to have updated before
ending. It is the fastest path to current state and links out to full detail
rather than duplicating it. If it's missing, stale, or you're not confident it
reflects reality, rebuild the same picture from primary sources — not from a
prior session's summary, and not from a leftover plan file, both of which can
go stale the moment work actually ships. Concrete example: a plan-mode file
for a five-step "natural dialogue" effort can still be sitting in a plan-mode
history after all five steps were designed, measured, and committed across
`9f36e7c`..`5455c2c` — trust `git log`, not the plan file, to know whether
something is still to do.

1. `git log --oneline -30` — what actually shipped most recently. If a
   commit's one-line summary doesn't tell you enough, `git show <hash>` or
   read its `CLAUDE.md` entry.
2. The tail of `CLAUDE.md` (`grep -n '^## ' CLAUDE.md | tail -20`, then read
   the newest entries in full) — dated, evidence-based records of what was
   built, measured, shipped, shipped-but-off, or deliberately deferred. This
   is the actual source of truth for current behavior and defaults, ranked
   above this guide, the README, and any other document when they disagree.
3. `docs/*-handoff.md` — explicitly flagged, not-yet-picked-up work items
   with acceptance criteria, left by a prior session specifically so a new
   agent or session (Claude Code or otherwise) can act on them without
   re-deriving context. Treat one as done once its acceptance criteria are
   met and a corresponding `CLAUDE.md` entry exists; delete or update it then
   rather than leaving a completed handoff doc looking open.
4. README's "Tuning knobs" table — current default/on/off state for every
   `SUBTITLE_AI_*` feature toggle. Several real features exist in code but
   ship **off** by default because their own measurement didn't clear the bar
   (e.g. `SUBTITLE_AI_TURN_DETECTION`, `SUBTITLE_AI_ASR_STYLE`) — "the code
   exists" is not the same claim as "this is what production does today."

If you are a different agent product (not Claude Code) picking this project
up cold, the same sources apply unchanged — none of them are Claude
Code-specific, and CLAUDE.md's naming is historical, not a scope restriction
on who should read it.

**Before ending your session**, update `docs/handover.md`: log every change
you made (one line, commit hash, pointer to its full `CLAUDE.md` entry) and
every issue or gap you noticed but didn't fix, even if it was out of scope for
what you were asked to do. See that file's own "How to log an entry" section
for the exact format. A session that ships a fix but leaves no trace in the
handover log has not actually handed over — the next session pays for it by
re-discovering the same thing from scratch.

## Working principles

1. **Read before changing.** Start with `README.md`, then the relevant module,
   tests, and the applicable section of `CLAUDE.md`.
2. **Preserve workflow boundaries.** Do not make Workflow B behave as though
   it has audio or ASR data, and do not use existing subtitles as transcription
   input for Workflow A.
3. **Measure translation or ASR changes first.** Use the existing evaluation
   scripts and real corpus data before changing defaults or adding heuristics.
4. **Prefer bounded recovery to guesses.** A quality refinement should have a
   defined activation condition, a safe fallback, and regression coverage.
5. **Keep state transitions explicit.** Return actionable errors and update
   job state through the store; do not create success-shaped fallbacks.
6. **Protect operational invariants.** Respect GPU coordination, atomic
   output, path containment, glossary locking, and cache-version semantics.

## Suggested specialist roles

| Role | Primary scope | Required checks |
|---|---|---|
| Product and API agent | API contract, job creation, validation, status semantics | API and job-store tests; preserve authentication and Origin protections. |
| Pipeline agent | ASR, translation, segmentation, projection, QC | Focused pipeline tests plus real-data evaluation when behavior changes. |
| Frontend agent | React pages, components, API hooks, operational UX | Type check and focused Vitest coverage; retain server-defined state semantics. |
| Glossary and cast agent | Entity protection, mining, metadata, glossary persistence | Protect manual entries; preserve evidence and separate-glossary-repo behavior. |
| Reliability agent | Workers, storage, cleanup, GPU, deployment paths | Exercise failure paths; never weaken atomicity, locks, or retention safeguards. |
| Documentation agent | README and `docs/` accuracy | Verify claims against implementation and distinguish historical records from current behavior. |

## Change workflow

1. Identify the user-visible behavior and all affected layers.
2. Inspect existing patterns and tests before introducing a helper or config.
3. Implement the smallest complete change across API, worker, UI, tests, and
   documentation as applicable.
4. Validate with the narrowest test command that covers the behavior; expand
   when changes cross component boundaries.
5. For translation, ASR, or quality heuristics, record the real-data evidence,
   false-positive tradeoffs, and any cache invalidation requirement.

## Guardrails

- Do not add protected entities merely because they recur; require the
  established transcript and mistranslation evidence.
- Do not silently drop malformed remote translation responses or partial
  output.
- Do not change default GPU behavior without checking the dedicated-versus-
  shared configuration and its measured consequences.
- Do not use broad exception handling to hide a failed write, database update,
  or pipeline stage.
- Do not modify deployment-owned glossary data with a lossy YAML writer.
- Do not alter completed historical plan documents merely to make them look
  current; update the live operational record when a change ships.

## Validation reference

```bash
# Backend
PYTHONPATH=subtitle_ai uv run --with pytest pytest tests -q

# Frontend
cd frontend && npx tsc --noEmit && npm test -- --run
```

Use focused test selections during development. Run the full relevant suite
before declaring a cross-cutting change complete. Documentation-only changes
do not require these commands unless a documentation check is introduced.
