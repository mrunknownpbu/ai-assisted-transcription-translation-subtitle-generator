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
7. **Operational, not decorative.** Information-dense but readable; minimal
   visual noise; predictable interactions; consistent spacing.
8. **Keyboard-friendly and accessible.** Native controls first, visible focus,
   text alongside colour, sufficient contrast, layouts that wrap on narrow screens.

## Information hierarchy

Each screen answers one question and does not mix operations with configuration:

| Screen | Question it answers |
|---|---|
| Library | What media needs subtitle processing? |
| Jobs | What is happening now, and what needs attention? |
| Job detail | What exactly was generated, and what needs correction? |
| Series | What terminology and metadata govern this series? |
| Translate Subtitle | How do I translate an existing subtitle? |

| Surface | Primary information | Secondary information |
|---|---|---|
| Library | Media name, type, and subtitle availability | Audio selection, batch selection, overwrite controls |
| Translate Subtitle | Source subtitle eligibility and match status | Upload source, episode matching, overwrite choice |
| Jobs | Status, stage, progress, and review count | Timing, language, concise event summary |
| Job detail | Final state, QC findings, logs, and output | Text-only subtitle editor for completed jobs |
| Series | Series identity and glossary state | Mined suggestions and cast-enrichment evidence |

## Tokens

`frontend/src/styles/tokens.css` is the only place raw colours, scales and
durations are defined. `global.css` and components use the variables. A test
(`styles/tokens.test.ts`) fails on a hex or `rgb()` colour anywhere else and on an
inline `style` other than the progress-bar width.

**Colour semantics**

| Token | Meaning | Used for |
|---|---|---|
| `--color-success` | success | completed, confirmed |
| `--color-warning` | needs attention | flagged cues, review count |
| `--color-error` | failure | failed jobs, error toasts |
| `--color-info` | informational / interactive | links, active nav, primary actions |
| `--color-neutral` | inert | muted text, idle |
| `--color-running` | job is running | = info |
| `--color-queued` | job is waiting | = neutral |
| `--color-cancelled` | job was cancelled | = neutral |
| `--tint-*` | row backgrounds | selected, subtle highlight, flagged |
| `--color-surface`, `-hover`, `--color-border` | surfaces | panels, rows, rules |
| `--color-text`, `-muted`, `-on-accent` | text | |

Status is never conveyed by colour alone: every badge and stage shows its text.

**Scales**

| Group | Tokens |
|---|---|
| Spacing (4 px base) | `--space-1` 4, `-2` 8, `-3` 12, `-4` 16, `-5` 24, `-6` 32 |
| Radius | `--radius-sm` 4, `-md` 6, `-lg` 8, `-badge` 10, `-pill` 14 |
| Type | `--font-sans`; sizes `--font-size-xs` 11, `-sm` 12, `-md` 13, `-base` 14, `-lg` 18; `--line-height-base` 1.5 |
| Elevation | flat; only floating layers use `--shadow-floating` |
| Motion | `--motion-progress` (progress bar width); no other animation |
| Breakpoint | 800 px (the two-column workspace collapses). CSS variables cannot be used in media queries, so it is repeated literally. |
| Component sizing | rows and controls size to content with `--space-*` padding; no fixed heights |

Existing declarations in `global.css` still use literal spacing, radius and font
sizes that match the scale; they migrate to the tokens when a rule is touched. The
colour rule is enforced; the spacing and type rules are not.

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

## Component catalogue

What exists in `frontend/src/components/` and what each is for. Add a shared
component only when a second use appears.

| Component | Purpose |
|---|---|
| `JobStatusBadge` | The one rendering of job state (text + colour). |
| `ProgressBar` | Server-provided stage and progress. No client-invented progress. |
| `JobTable`, `ManualGlossaryTable`, `GlossarySuggestionsTable` | Operational tables with row actions beside the item. |
| `MediaBrowser`, `AudioStreamPicker` | Library navigation and audio-stream choice. |
| `SrtEditor` | Text-only cue editing. |
| `QcFindingsList`, `CastReportPanel` | QC and cast-evidence views. |
| `Toast` | Request feedback. |

Not built yet (tracked in the handover, build when a screen needs them): a
dialog, a tabs component (tabs are CSS-only today), a shared empty/error/loading
state, and a job timeline.

## See also

`docs/architecture.md` (frontend layers), `docs/api.md` (the shapes these
components render), `docs/product-requirements.md` (what the screens must
answer), `docs/handover.md`.
