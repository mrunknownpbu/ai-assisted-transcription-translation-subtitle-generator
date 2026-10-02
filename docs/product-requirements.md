# Product Requirements Document

The authoritative statement of what Subtitle AI is and must do, independent of
how it is built. Architecture is in `docs/architecture.md`; what shipped, what is
open and what was measured is in `docs/handover.md` and `docs/decisions/`.
Where a quantity is not yet known it is marked `TBD - requires measurement`; no
number here is invented.

## 1. Vision

Subtitle AI is a self-hosted application that generates English subtitles for
long-form video (television episodes, films) using local GPU-accelerated AI, for
media that has no subtitles in a language the viewer can read, or whose subtitles
need replacing.

Primary goals, in priority order:

1. **Transcription fidelity**: what is on the audio is what is subtitled.
2. **No invented text**: ASR hallucinations and non-speech are suppressed, not
   translated.
3. **Accurate timing**: cues appear when the speech does.
4. **Reliable translation**, with character names and terminology preserved.
5. **Safe file handling**: a failure never damages an existing subtitle.
6. **Unattended batch processing** of a season or a library.
7. **Transparent quality reporting**: the user can see what needs review.

## 2. Users

| User | Need |
|---|---|
| Self-hosted media administrator (primary) | Queue an episode, a season or a folder; trust the result enough to leave it; see what failed. |
| Home-lab operator | Run GPU-heavy work on shared or dedicated hardware without crashing other workloads; recover from restarts. |
| Media-automation operator | Have Sonarr/Radarr identify media, and Plex/Jellyfin pick up new subtitles. |
| Subtitle reviewer | Find the few cues that need a human; correct text in place. |
| Series maintainer | Stop names and recurring terms being translated as ordinary words. |

Enterprise requirements (multi-tenant access, role-based permissions, audit
trails) are not part of this product.

## 3. Workflows

### Workflow A: video to English subtitle

**Principle: audio is the only source of truth.** Existing subtitles, embedded
subtitle tracks, filenames, episode titles and other text may supply metadata or
evidence (for example which series this is, or a character list), but never
transcription input. Workflow A must not read a subtitle to influence ASR, copy
one into the transcript, silently fall back from ASR to one, or use one to repair
ASR output.

1. Identify the media (path, Sonarr/Radarr, series tags).
2. Inspect the media (streams, duration).
3. Select the audio stream (automatic recommendation by sampled language, or the
   user's choice).
4. Extract the audio.
5. Detect the language (or take the user's).
6. Transcribe (faster-whisper).
7. Suppress hallucinated and non-speech content.
8. Detect code-switched stretches.
9. Translate to English.
10. Protect glossary and cast terminology.
11. Re-segment into subtitle cues.
12. Re-time cues against the speech.
13. Run quality control.
14. Write the English `.srt` atomically (and the source-language `.srt` beside the
    video).
15. Ask the media server to rescan (best effort).
16. Report completion or failure.

### Workflow B: existing subtitle to English subtitle

**Principle: the subtitle is the source document.** It is translated directly;
**ASR never runs.** The source file is committed beside the video as that
episode's original-language subtitle.

1. Accept a library subtitle or an upload (SRT or WebVTT, UTF-8, at most 2 MiB).
2. Parse it.
3. Validate its structure; a malformed source fails the job instead of being
   silently truncated.
4. Detect or validate the source language.
5. Protect glossary and cast terminology.
6. Translate.
7. Preserve source timing, repaired only by explicit documented rules.
8. Run quality control.
9. Write the English `.srt` atomically.
10. Preserve the original-language subtitle beside the video (its overwrite policy
    is independent of the English output's).
11. Ask the media server to rescan (best effort).
12. Report completion or failure.

## 4. Functional requirements

**Jobs.** Single-file and batch processing (a batch preserves failed selections
and says why an item cannot be queued). A queue with live status. Retry creates a
new job and never mutates the old one. Cancellation of queued and running jobs
(a running job stops at its next checkpoint and leaves existing outputs intact).
Progress with the current stage. A history of past jobs, including failures, with
their errors and the configuration they ran with. Interrupted jobs are recovered
after a restart, with a bounded number of automatic re-runs.

**Subtitles.** Upload. Replacement and preservation are explicit and independent
per output (source language, English); preservation is the default. Text-only
in-place editing of a finished English subtitle (timings and cue count
unchanged).

**Translation.** English is the only target. Entities are protected before
translation and restored after; forced phrase translations apply only to exact
configured source text. Code-switched stretches are translated in their own
detected language, not the dominant one.

**Glossary and cast.** Per-series glossary with manual entries. Mined candidates
are suggestions for review only. Automatic protection requires all three of:
cast metadata credits the name, it recurs in transcripts, and an unprotected
translation demonstrably got it wrong.

**Library.** Browse media, see which items have English subtitles, inspect audio
streams, view series.

**Quality.** Per-job QC results by stage, with high-confidence findings
surfaced as a review count. QC is advisory (below).

**Integrations.** Sonarr/Radarr (identify media), TMDB/TVDB/IMDb/NFO (cast),
Plex/Jellyfin (rescan), a failure webhook.

| Integration | Role | If it fails |
|---|---|---|
| Media filesystem | Required | The job fails with a typed error. |
| Sonarr / Radarr | Optional | Falls back to folder tags. |
| TMDB / TVDB / IMDb / NFO | Optional | Cast enrichment skipped; nothing invented. |
| Plex / Jellyfin | Best effort | Logged; never fails or delays a job. |
| Failure webhook | Best effort | Logged; never affects the job. |
| Remote translate server | Optional | Falls back to local translation. |

## 5. Non-functional requirements

| Area | Requirement | Measure |
|---|---|---|
| Correctness | A completed job's output is a syntactically valid SRT with non-overlapping, ordered cues. | Output validation on every write. |
| Correctness | No text is invented: suppressed hallucinations never reach the translation. | Hallucination QC and signature tests; coverage on real corpora is `TBD - requires measurement`. |
| Reliability | A failed, cancelled or crashed job leaves any existing subtitle byte-identical. | Atomic-write tests. |
| Reliability | A restart recovers interrupted jobs; a bounded number of automatic re-runs, then a typed failure. | Job-store recovery tests. |
| Determinism | The same audio, configuration and models give the same transcript and subtitle. | Transcript cache keyed by media, stream, model and pipeline version; bit-for-bit repeatability on GPU is `TBD - requires measurement`. |
| Reproducibility | A job records the configuration it ran with. | `config_snapshot` on every started job. |
| Observability | Every job log line carries the job ID; a failure names the stage and a stable error code. | Log and error tests. |
| Security | See section 6. | |
| Performance | Throughput for a long episode on the reference GPU. | Real-time factor and per-stage time: `TBD - requires measurement` (per-stage durations are recorded per job; a benchmark protocol is not yet fixed). |
| GPU memory safety | A model is never loaded without a VRAM pre-check, and GPU use is serialised across processes. | Lock and pre-flight tests; failures are typed `GPU_RESOURCE_ERROR`. |
| Filesystem safety | Paths stay inside the media root; protected files are never overwritten; uploads are size and type limited. | Path and upload tests. |
| Concurrency | One GPU job at a time per GPU; API remains responsive during a job. | Lock tests. |
| Maintainability | Lint and type checks run with no exclusions; documentation claims are checked against code where practical. | CI. |
| Testability | Stages are unit-testable with fakes; model-dependent behaviour has controlled fixtures and explicit tolerance. | Test suite (`docs/testing.md`). |

## 6. Security requirements

- The API must not be served unauthenticated beyond loopback by default: with no
  key set, the server refuses to listen on a non-loopback address unless the
  operator explicitly accepts an open API on a trusted network.
- With a key set, every route except health requires it.
- File paths from clients are validated against the media root.
- Uploads are limited in size and type.
- Webhooks and remote URLs are operator-configured, never request-supplied.
- Secrets live in the environment, not in job records or logs.

The threat model and assumptions are in `docs/architecture.md` ("Security model").

## 7. Quality control

QC is **advisory**. A job completes when its output is structurally valid; QC
exposes problems for a human instead of silently discarding or rewriting. QC
covers: repeated text, hallucination indicators, excessive cue length,
impossible timing, abnormal reading speed, empty cues, untranslated segments,
suspicious translations, broken formatting, overlapping cues and malformed
structure. QC never edits a subtitle; a transformation stage that changes text
owns that behaviour explicitly.

## 8. Non-goals

- Targets other than English.
- A fully automatic quality gate that replaces human judgement.
- Using existing subtitles as ASR input in Workflow A.
- Automatically protecting every frequent capitalised word as a name.
- Editing timing or cue structure in the browser editor.
- Multi-user accounts, a hosted service, or scaling beyond one GPU host.

## 9. Constraints

- GPU memory is finite and must be coordinated across processes.
- The workload is long-form media: throughput and batch processing matter.
- Source dialogue can be multilingual, noisy, unpunctuated or contain ASR
  artefacts.
- Glossary data is deployment-owned and may be a separate Git repository.

## 10. Acceptance criteria for new product work

1. Both workflows keep their separate source-of-truth rules.
2. Behaviour changes have focused tests (backend and/or frontend).
3. Failures are observable through the API, job state or logs, with a typed code.
4. Documented behaviour changes, and measured trade-offs are recorded in
   `docs/decisions/`.
5. A change to a default is evaluated on real subtitle or transcript data first.
