# Design System

## Purpose

The web UI is an operational console for media processing, not a general
consumer application. Its design should make job state, irreversible actions,
and review requirements easy to understand while keeping long media lists
scannable.

The implementation lives in `frontend/src/`, with shared styles in
`frontend/src/styles/global.css`, reusable UI in `frontend/src/components/`,
and route-level screens in `frontend/src/pages/`.

## Design principles

1. **State is visible.** Show queue status, pipeline stage, progress, errors,
   and review counts where users make decisions.
2. **Keep media operations safe.** Default to preserving existing subtitles;
   make replacement choices explicit.
3. **Explain unavailable actions.** A disabled batch item should state why it
   cannot run instead of silently disappearing.
4. **Optimize for scanning.** Media and jobs are often reviewed as lists, so
   prioritize compact rows, stable ordering, and concise summaries.
5. **Do not imply certainty the system lacks.** Progress is directional and
   QC is advisory; UI copy must not describe either as a guarantee.
6. **Retain user work.** Failed queue actions stay selected, and form state
   should survive recoverable request failures.

## Information hierarchy

| Surface | Primary information | Secondary information |
|---|---|---|
| Library | Media name, type, and subtitle availability | Audio selection, batch selection, overwrite controls |
| Translate Subtitle | Source subtitle eligibility and match status | Upload source, episode matching, overwrite choice |
| Jobs | Status, stage, progress, and review count | Timing, language, concise event summary |
| Job detail | Final state, QC findings, logs, and output | Text-only subtitle editor for completed jobs |
| Series | Series identity and glossary state | Mined suggestions and cast-enrichment evidence |

## Components and behavior

- **Status badges:** use `JobStatusBadge` for a consistent, compact rendering
  of job lifecycle state. Do not duplicate status-to-label mapping in pages.
- **Progress:** use `ProgressBar` with the server-provided stage and progress.
  Do not manufacture client-side progress for a running job.
- **Toasts:** use the shared `Toast` mechanism for request feedback. Pair an
  error toast with an actionable recovery path when one exists.
- **Tables:** use existing job, glossary, and suggestion table patterns for
  sortable operational data. Keep row actions adjacent to the item they
  affect.
- **Editors:** `SrtEditor` edits only cue text. It must not expose controls
  that imply timing, cue-count, or pipeline-result changes.
- **Forms:** validate required choices before submission, retain values after a
  failed request, and expose server validation messages.

## Status and feedback conventions

| Condition | UI treatment |
|---|---|
| Queued or running job | Status badge plus live stage and progress. |
| Completed job with findings | Completed status remains distinct from the advisory review count. |
| Failed or cancelled job | Clear terminal state, relevant error/log context, and retry action if available. |
| Existing subtitle would be replaced | Explicit overwrite control; preservation is the default. |
| Item cannot be batch processed | Disabled selection with the specific reason visible. |
| Unauthorized request | Prompt for the configured API key through the existing client flow. |

## Accessibility and responsive behavior

- Use semantic buttons, labels, tables, and form controls before introducing
  custom interaction patterns.
- Keyboard focus must remain visible, particularly after opening dialogs,
  toasts, or the subtitle editor.
- Do not communicate lifecycle or QC meaning with color alone; include text or
  an icon with accessible labeling.
- Preserve readable layouts for narrow screens by allowing tables and media
  rows to wrap rather than clipping state or actions.

## Implementation rules

- Reuse types and API hooks from `frontend/src/api/`; do not create
  page-specific response shapes.
- Put reusable behavior in components and keep pages responsible for route
  composition and page-level state.
- Add Vitest coverage beside the affected UI behavior. Test user-observable
  states: loading, empty, disabled, success, and failure where relevant.
- Keep UI copy aligned with actual backend semantics, especially overwrite,
  advisory QC, and job terminal states.

## See also

`docs/handover.md` (start here — the current session-to-session log),
`docs/architecture.md` for the API/data shapes these components render,
`CLAUDE.md` for dated records of UI-affecting backend decisions (e.g. what a
status or QC field can actually contain), and `docs/agents.md`'s handover
section for how to reconstruct current state at the start of a session.
