# Product Requirements Document

## Product summary

Subtitle AI creates English subtitle files for long-form video. It supports
two workflows:

1. Video transcription: video audio is transcribed, translated, segmented,
   quality-checked, and written as an English SRT.
2. Subtitle translation: an existing original-language SRT or WebVTT file is
   translated to English without performing ASR.

The product is designed for television episodes and films where timing,
dialogue coverage, and character-name fidelity matter as much as translation
fluency.

## Users and needs

| User | Need | Product response |
|---|---|---|
| Library operator | Process one episode or a season without manual media preparation. | Browse media, inspect audio streams, queue single or batch jobs, and keep or replace existing subtitles. |
| Subtitle reviewer | Find and correct the small number of cues that need editorial attention. | Surface advisory QC findings and permit text-only edits to completed English SRTs. |
| Series maintainer | Stop names and recurring terms from being translated as ordinary words. | Manage a per-series glossary, review mined suggestions, and inspect cast-enrichment evidence. |
| System operator | Run GPU-intensive work safely and understand job state. | Serialize GPU work, expose progress and health, preserve failed artifacts temporarily, and write outputs atomically. |

## Goals

- Produce valid, timed English SRT output while preserving dialogue coverage.
- Keep source-language subtitles and English output behavior explicit and
  independently configurable.
- Preserve verified character names and glossary terms verbatim.
- Make pipeline status, queue state, retries, cancellation, and QC visible.
- Favor bounded, evidence-based quality improvements over speculative
  linguistic rewriting.
- Prevent a failed job from corrupting an existing subtitle file.

## Non-goals

- Multi-target output. English is the supported target language.
- Replacing human editorial judgement with a fully automatic quality gate.
- Treating existing subtitles as an ASR source for video-transcription jobs.
- Automatically protecting every frequent capitalized word as a name.
- Editing subtitle timing or cue structure in the browser editor.

## Functional requirements

### Job creation and processing

- Users can queue video jobs from the media library and choose an audio stream
  when automatic selection is insufficient.
- Users can queue SRT or WebVTT translation jobs from library files or uploads.
- Batch operations preserve failed selections and report why an item cannot be
  queued.
- Existing source and English subtitle handling must be explicit: source and
  translated output overwrite policies are independent.
- Jobs must expose queued, running, completed, cancelled, and failed states,
  plus live stage and progress information.

### Translation quality

- The pipeline must protect configured entities before translation and restore
  their canonical spelling afterward.
- Forced phrase translations may apply only to exact configured source text.
- Translation, segmentation, timestamp projection, and QC must run before
  final output is written.
- QC is advisory: structural output failures prevent completion; translation
  and readability findings identify review work rather than silently discard a
  job.

### Review and glossary management

- Completed jobs can expose final subtitle cue text for in-place editing.
- Edits must preserve cue timings, cue count, and unaffected line breaks.
- Series pages must support manual glossary entries, auto-mined suggestions,
  and cast-enrichment reports with provenance.
- Automatically enriched names require credited metadata, recurring
  transcript evidence, and a demonstrated unprotected translation failure.

## Success measures

- Completed jobs produce syntactically valid SRT files with atomic replacement.
- Existing outputs remain intact when a job fails, is cancelled, or validation
  fails.
- The queue communicates meaningful pipeline state without requiring log
  inspection.
- High-confidence entity, hallucination, and output issues are discoverable
  in job QC.
- Quality and performance changes are evaluated against real subtitle or
  transcript data before becoming defaults.

## Constraints

- GPU memory is finite and model loading must coordinate across processes.
- The primary workload is long-form media, so throughput and batch processing
  matter.
- Source dialogue can be multilingual, noisy, unpunctuated, or contain ASR
  artifacts.
- Glossary data is deployment-owned and may be a separate Git repository from
  this application.

## Acceptance criteria for new product work

1. Preserve both workflows and their separate source-of-truth rules.
2. Add focused backend and/or frontend tests for behavior changes.
3. Keep failures observable through API responses, job state, or logs.
4. Document user-visible behavior, operational implications, and any measured
   tradeoff in the appropriate project documentation.

## See also

This document states durable product intent; it does not track what has
shipped, what's mid-flight, or what's deliberately deferred. For that:
`docs/handover.md` (start here — the current session-to-session log),
`CLAUDE.md` (dated, evidence-based operational record — the source of truth
when it disagrees with this document), `README.md`'s "Tuning knobs" table
(current feature-toggle defaults), and any `docs/*-handoff.md` files (open
work already scoped for a future session). See `docs/agents.md`'s handover
section for how to reconstruct current state at the start of a session.
