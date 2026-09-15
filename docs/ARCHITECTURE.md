# Architecture

## Principle

Audio is the sole source of truth. Existing subtitles, filenames, and embedded metadata
(container language tags, subtitle streams) are recorded as **evidence only** — visible in
provenance for debugging, never fed into transcription, language ID, or timing.

The one deliberate exception is the **`direct_translation` job type** (see Stage 8): a user
can upload a subtitle file they already have and translate its text directly. This never
claims the file was verified against any audio — its provenance is explicitly labeled
"not verified against audio" everywhere it surfaces (API, GUI, output sidecar) — it's a
separate, clearly-marked utility, not a mode of the audio-first pipeline.

## Pipeline stages

Each stage lives in `backend/app/pipeline/stage<N>_.../` and communicates only through the
typed dataclasses in `backend/app/pipeline/interfaces.py`. No stage reaches into another
stage's internals or the database directly — this is what makes every stage independently
unit-testable and swappable.

| # | Stage | Module | Output type |
|---|-------|--------|-------------|
| 1 | Media Inspection | `stage1_inspection/inspector.py` | `MediaInspectionResult` |
| 2 | Audio Stream Selection | `stage2_stream_selection/ranker.py` | `StreamSelectionResult` |
| 3 | Language Detection | `stage3_language_detection/detector.py` | `LanguageDetectionResult` |
| 4 | ASR (fallback chain) | `stage4_asr/fallback.py` + `engines/*` | `ASRResult` |
| 5 | Canonical Transcript | `stage5_canonical_transcript/assembler.py` | `CanonicalTranscript` |
| 6 | Hallucination Defense | `stage6_hallucination_defense/defense.py` | annotates transcript + `SuppressionDecision[]` |
| 7 | Normalization | `stage7_normalization/normalizer.py` | annotates transcript (`normalized_text`) |
| 8 | Translation (fallback chain) | `stage8_translation/translator.py` + `engines/*` | `TranslatedChunk[]` |
| 9 | Target Segmentation | `stage9_target_segmentation/{chunker,segmenter}.py` | `SourceChunk[]` / `TargetCueDraft[]` |
| 10 | Timing Projection | `stage10_timing_projection/projector.py` | `TargetCue[]` |
| 11 | QC & Output | `stage11_qc_output/{qc,formatters/*}.py` | `QcReport` + files |

`app/jobs/worker.py`'s `JobRunner` is the only module that knows this order — it wires the
stages together for one job and writes progress/results to the DB as it goes.

## Stage 1 — Media Inspection

`ffprobe` enumerates every stream in the container, regardless of format (mp4/mkv/mov/...)
or stream count. Each audio stream gets a **content-derived hash** (`stream_hash`):
`sha256(codec|channels|sample_rate|duration|sha256(first 8s of decoded PCM))`. This is the
cache key used everywhere downstream — renaming or re-muxing a file does not change it;
replacing the audio does. Subtitle streams found are recorded as `existing_subtitle_streams`
(evidence only).

## Stage 2 — Audio Stream Selection

Each stream is decoded to mono 16kHz PCM and scored on three signal-processing features
computed directly from the waveform (`ranker.py`):

- **Speech band energy ratio**: FFT energy concentrated in 300–3400Hz (human speech
  formants) vs. total energy.
- **Voiced frame ratio**: a cheap energy+zero-crossing-rate heuristic — speech alternates
  between voiced/unvoiced frames; steady music or tone beds don't.
- **Dynamic range**: spread between the 95th and 5th percentile frame RMS in dB.

`dialogue_score = 0.45*band_ratio + 0.40*voiced_ratio + 0.15*normalized_dynamic_range`.
Track titles, language tags, and stream order are never inputs. Manual override
(`selected_by='manual'`) replaces the selection without touching the computed rankings.

## Stage 3 — Language Detection

Uses Whisper's own language-ID head via `faster-whisper` (`FasterWhisperLanguageId`) on a
30-second sample — the same acoustic model family as transcription, so no second
independently-maintained language-ID model is needed. Manual override
(`apply_manual_override`) never discards the original detection; it's tracked alongside as
`overridden=True` with the auto-detected value preserved for audit.

## Stage 4 — ASR with configurable fallback

`stage4_asr/fallback.py`'s `transcribe_with_fallback` walks a configured engine list
(`SUBTITLE_ASR_ENGINE_CHAIN`, default `faster_whisper → openai_whisper → whisper_cpp →
whisper_api → vosk`). For each engine: check `is_available()` (cheap, local — package
importable, binary on PATH, model file present, API key configured); if unavailable, skip
with a logged reason; if available, attempt `transcribe()`, catching engine-specific
failures. First success wins. Every attempt (skipped/failed/succeeded) is recorded as an
`EngineAttempt` and stored in the transcript's provenance — a transcript always says which
engine actually produced it and what was tried first. Total exhaustion raises
`ASRUnavailableError` carrying the full attempt list.

`faster_whisper` is primary (CTranslate2 reimplementation of Whisper weights — much faster
and lower-memory than the reference implementation on both CPU and GPU).
`hardware.recommended_whisper_model()` auto-downgrades the requested model size when
detected VRAM is too small (or unconditionally caps at `small` on CPU-only), so a job
degrades gracefully instead of OOMing.

## Stage 5 — Canonical Transcript

Pure adapter (`assembler.py`): wraps the winning `ASRResult` into mutable
`CanonicalSegment`s and assembles the full provenance record (stream id, engine, model
version, all engine attempts, language detection method/confidence, hardware profile,
timestamp). No transcription logic lives here.

## Stage 6 — Hallucination Defense

Every segment gets a single weighted **score in [0, 1]** combining several independent
signals via `max()` (any one strong signal raises suspicion) plus an additive cross-segment
recurrence bonus. Suppression only happens at `score >= suppression_score_threshold`
(default `0.75`, deliberately high) — this scoring approach (`defense.py`) replaced an
earlier binary AND-gate design, ported from a proven production implementation and extended
with this platform's own acoustic VAD signal, which that source design didn't have:

1. **Known-artifact signature match** — a regex registry (`signatures.py` +
   `default_signatures.json`) of *specific* text patterns a model has been observed to
   hallucinate for a given language (e.g. a real, documented case: a Turkish subtitle-credit
   line injected into audio that never said it). Each entry records the real evidence that
   confirmed it — new entries should too, not guessed patterns.
2. **No-speech contradiction** — the ASR engine's own `no_speech_prob` says it doubted
   speech was present, yet emitted text anyway (`>= 0.6` → weight `0.4`).
3. **Degenerate compression ratio** — `gzip(text)` compresses unusually well, the classic
   signature of a decoder stuck in a repetitive loop (`>= 2.4` → weight `0.5`).
4. **Low average log-probability** — poor per-token confidence throughout the segment
   (`<= -1.0` → weight `0.3`).
5. **Low word confidence + no VAD voice activity** — this platform's own signal: quiet real
   speech has low confidence but IS real, and loud non-speech noise can trip VAD without
   hallucinated text, so this only fires when both agree (weight `0.6`).
6. **Within-segment repetition loop** — the same short phrase repeated ≥4 times
   consecutively, largely independent of confidence (weight `0.9`).
7. **Cross-segment recurrence** (additive bonus, not a `max()` floor) — near-identical text
   recurring verbatim across many segments in the same job reinforces whatever other signal
   already fired; pure recurrence with no other signal never suppresses by itself.

No single acoustic signal (2–5 above) suppresses alone — only a signature match, a
repetition loop, or several signals reinforcing via recurrence cross the threshold. This
mirrors the production design being ported from and keeps the false-negative-minimization
bias explicit at the scoring level, not just the decision level.

Suppression never deletes anything: `CanonicalSegment.suppressed=True`, `suppression_reason`,
and `hallucination_score` are set, but `text`/`words` stay intact. Every segment scoring
`>0` (suppressed or not) is logged; a segment scoring exactly `0` isn't logged at all, to
keep the audit trail meaningful. Ambiguous cases are kept as `kept_marginal`, never dropped
— auditable either way via the `suppressions` DB table.

## Stage 7 — Normalization

Conservative orthographic cleanup only (`normalizer.py`): whitespace/punctuation spacing
fixes applied universally, plus a small per-language ruleset (currently just English
standalone-`i` capitalization). Never rewrites wording, never drops filler words. The
original `text` field is never touched — only `normalized_text` is written, so the raw
utterance record always survives untouched for audit.

## Stage 8 — Translation with configurable fallback + dynamic languages

Mirrors the ASR fallback pattern (`translator.py`, `EngineAttempt` reused from
`interfaces.py`). Primary engine is local NLLB-200
(`facebook/nllb-200-distilled-1.3B` by default — see `docs/DEPLOYMENT.md` for the size/
resource tradeoff) via `transformers` — fully self-hosted, no API key, ~200 languages.
`language_codes.py` resolves user input (ISO 639-1 code or common English name) to a
FLORES-200 tag via a convenience table; anything not in the table can be supplied directly
as a raw FLORES-200 tag (`xxx_Xxxx`) — this is what keeps "any language on demand" honest
without hand-maintaining all ~200 entries. An optional API-based engine
(`ApiTranslationEngine`) is wired into the same fallback chain, disabled by default (no key
configured).

Generation is beam search (`num_beams`, default 4) with `no_repeat_ngram_size` (default 4)
— both confirmed in a real production incident to prevent NLLB's degenerate
repetition-loop failure mode (a real case: an input mentioning a word 3 times produced
~13x repetitions of an unrelated sentence). A `torch.cuda.OutOfMemoryError` during
generation triggers one halving-retry (`max_new_tokens` cut in half after clearing the CUDA
cache); if that still fails, the error correctly falls through to the next configured
engine in the fallback chain.

**Entity/glossary protection** (`glossary.py`, optional, gated on the job supplying
entries): named entities are swapped for opaque ASCII placeholders (`Xaa`, `Xab`, …) before
translation and restored after, so the translation model never sees — and can never
mistranslate or transliterate — a protected name. Boundary matching is custom (not
Python's Unicode-aware `\b`) specifically so it works against scripts with no whitespace
(e.g. Japanese) without false-matching inside a character run. A purely structural,
no-model-call recovery pass (`recover_dropped_entities`) reinserts a name the translation
under-counted relative to the source — deliberately never calls a model for this repair,
since an earlier version that did was itself observed to hallucinate free-form text.
Entities come from `Job.glossary_entities` (inline, user-supplied) and/or optional TVDB
cast enrichment (`tvdb_client.py`, `Job.tvdb_id`) — entirely best-effort: no API key
configured means the job proceeds with only the manually-supplied entries, never fails.

## Stage 9 — Target Segmentation

Two-part, in `stage9_target_segmentation/`:

- `chunker.group_into_source_chunks`: groups **kept** (non-suppressed) canonical segments
  into `SourceChunk`s by pause gap between consecutive words (`chunk_pause_gap_s`, default
  0.7s) — giving the translation engine a full breath-group of context instead of single
  short ASR segments. Also caps a chunk at `max_chunk_chars`/`max_chunk_segments` (default
  300 chars / 6 segments) independent of pause gap, so a long dialogue run with only short
  pauses can't silently grow past the translation engine's practical input length and lose
  the overflow to tokenizer truncation — capping happens here, before translation.
- `segmenter.segment_into_cue_drafts`: re-cuts the *translated* text into subtitle-sized
  cues using character-budget rules (`max_chars_per_line * max_lines_per_cue`). A long
  translated chunk splits into several cues (1→N); two short, temporally-adjacent chunks
  merge into one cue (M→1). This is the genuine N:M mapping the spec requires — never
  forced 1:1 with source segments.

## Stage 10 — Timing Projection

`projector.project_timing` derives every cue's start/end strictly from the source chunk's
real word-timestamp span (`SourceChunk.word_span`) — never a global shift, never
Subsync-style external alignment. A split-chunk cue gets a proportional slice of the
chunk's duration sized by its character share; a merged cue spans the union of its source
chunks' timestamps. A minimum-duration floor is applied for readability, clamped so it can
never overlap the next cue — if the gap is too tight, the cue stays short and QC (Stage 11)
flags it, rather than this stage inventing time that isn't in the source.

## Stage 11 — QC & Output

Four independent QC passes, each producing its own `QcReport` row (same `QcFinding`
contract throughout) so a job's QC trail shows exactly which stage of processing a problem
was caught at:

- `qc.run_qc` — cue-level checks on the final `TargetCue`s: empty text, minimum duration,
  reading speed in chars/sec, line count, line length, adjacent-cue overlap.
- `qc_translation.run` — content checks on the `TranslatedChunk`s, right after generation:
  empty translation, a leaked glossary placeholder (`restore()` failed to clean one up),
  unbalanced brackets, length-ratio outliers (short-string-guarded to avoid false positives
  on brief cues), a trigram degenerate-repetition detector (belt-and-suspenders on top of
  Stage 8's `no_repeat_ngram_size` generation-time fix — an independent safety net in case
  that's ever bypassed), and cross-chunk duplicate-translation detection (two *different*
  source texts producing the identical translation, a batch-collapse signature).
- `qc_entity.run` — only runs when a job supplied a glossary: reconciles each protected
  entity's occurrence count between source and translated text.
- `qc_output.run` — re-parses the SRT file this platform just wrote, independently of the
  renderer that produced it, and validates it as a final gate (empty cue, non-positive
  duration, overlap) — catches anything a rendering bug could introduce that the
  in-memory cue checks above wouldn't see.

Every check records **every** cue/chunk's outcome — not just failures — so a QC report is a
complete, auditable trail. Formatters (`formatters/`):

- `srt.py` / `vtt_webvtt.py`: SRT and WebVTT (the `.vtt` and `.webvtt` file extensions are
  both produced from the same WebVTT renderer — identical content, per the spec's explicit
  request for both extensions).
- `burned_in.py`: ffmpeg `subtitles` filter, `h264_nvenc` when the hardware profile reports
  an NVIDIA GPU (falling back to `libx264` once if NVENC fails for an unrelated reason),
  audio stream always copied untouched.
- `provenance.py`: every output gets a `<file>.provenance.json` sidecar (SRT has no comment
  syntax); WebVTT additionally gets an inline `NOTE` block with the same data.

## Job queue & concurrency

`app/jobs/queue.py` implements the queue as pure atomic SQL, not read-then-write:
`claim_next_job` does `UPDATE jobs SET status='running' WHERE id=? AND status='queued'` —
the `rowcount` tells the caller whether it won the race. GPU access is gated the same way
via a `gpu_slots` table (`try_acquire_gpu_slot`), sized at startup to
`gpu_count * SUBTITLE_MAX_CONCURRENT_GPU_JOBS`, so concurrent jobs can never oversubscribe
VRAM regardless of how many worker threads/processes are running. SQLite runs in WAL mode
so the API process and worker threads read/write concurrently without lock contention under
normal load. See `backend/tests/concurrency/` for the tests that exercise this under real
thread races.

## Hardware detection

`app/hardware.py`'s `detect_hardware()` never raises: it tries `torch.cuda.is_available()`
first (matching what the ASR/translation engines will actually see), falls back to parsing
`nvidia-smi` if torch's view disagrees with a present binary, then tries the equivalent for
ROCm, and finally reports a `CPU` profile with a `fallback_reason` if nothing is found.
Called once by the API process at startup (persisted to `hardware_profiles`) and once per
`JobRunner` instantiation, so a worker process on different hardware in a future multi-node
setup detects independently.

## Direct subtitle translation (`direct_translation` job type)

A second, clearly-separate job type alongside the 11-stage audio pipeline:
`POST /api/jobs/direct-translation` accepts a `.srt` file directly. `Job.job_type` routes
`jobs/worker.py`'s `JobRunner.run` to `_run_direct_translation` instead of
`_run_audio_pipeline`. What's different from the audio pipeline:

- **Input**: `pipeline/direct_translation/srt_io.py` parses the uploaded file
  (`formatters/srt.py`'s `parse_srt` — the same parser Stage 11's `qc_output` uses to
  re-validate its own output) and detects the source language from the cue *text* via
  `langdetect` — the one legitimate place in the platform that does text-based language ID,
  since there is no audio to run Whisper's LID on.
- **No ASR, no hallucination defense, no timing projection**: each input cue becomes its
  own `SourceChunk` one-to-one (`word_span` = that cue's own start/end), so the input
  file's timing is authoritative and untouched — this stage only ever replaces a cue's
  text.
- **Everything else is shared**: the same Stage 8 translation engines, glossary protection,
  and Stage 11 QC/formatters run unmodified — `_write_outputs` is one shared method used by
  both job types.
- **Provenance is explicit about what this is**: every output's sidecar and the GUI carry
  `"Translated from a user-supplied subtitle file — not verified against audio."` — this
  is what keeps the addition consistent with the platform's audio-is-truth principle rather
  than quietly blurring it.
- **Not supported**: burned-in video output (there's no source video to burn into).

## Live-processing readiness

Every pipeline stage consumes paths/streams by *identity* (`audio_stream_id`,
`stream_index`, a resolved file path) rather than assuming "this came from an uploaded
file." A `MediaSource` abstraction point is documented for future live/streaming ingestion
(`FileMediaSource` now; a `StreamMediaSource` would resolve to an RTMP/HLS URL instead of a
path) — batch is the only implemented and tested path today, but no stage's logic would
need to change to add it.
