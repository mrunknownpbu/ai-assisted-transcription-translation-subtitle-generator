# AI-Assisted Transcription & Translation Subtitle Generator

An automated pipeline that takes a video file with embedded audio,
transcribes the spoken dialogue, translates it, and produces a timed,
quality-checked subtitle file — with no manual transcription or
translation step. A second, independent workflow translates an
already-transcribed original-language `.srt` straight to English,
without any ASR step (see "Workflows" below).

It's built for long-form video (TV episodes, films) where subtitles either
don't exist in the target language or need to be regenerated, and is
tuned to avoid the two failure modes that make machine-generated
subtitles unusable in practice: hallucinated text and mistimed lines.

## Workflows

- **Workflow A — video transcription** (the pipeline below): video → audio
  → ASR → translation → English subtitle. Audio is the sole source of
  truth here; no subtitle file is ever read as transcription input.
- **Workflow B — subtitle translation**: an existing original-language
  `.srt` (e.g. a downloaded fansub, or a human-made subtitle) →
  translation → English subtitle. No ASR, no audio extraction -- the
  uploaded/selected `.srt` is already the transcription, and is never
  treated as though it came from ASR. Started from the "Translate
  Subtitle" page, it reuses the same translation engine, glossary, GPU
  lock, and job queue as Workflow A, but is a structurally separate
  pipeline (`srt_translation.py`). The source `.srt` is also committed to
  the library as that episode's own original-language subtitle (same
  sibling-pair shape a transcription job produces, e.g. `S01E01.tr.srt`
  + `S01E01.en.srt`) -- `overwrite_original` controls whether an existing
  one there is kept or replaced, independent of `overwrite_english` for
  the translated output.

## Web UI

The app serves a React UI (built into the image) on the same port as the
API (`8099` in `compose.yml`):

- **Library** (`/`) — browse the media library and queue a video for
  Workflow A, optionally choosing the audio stream ("Analyze" samples each
  track's spoken language) and whether to keep or replace existing
  subtitles. **Batch queue** mode adds checkboxes to video rows, marks
  episodes that already have an English subtitle with a ✓, and can queue
  every untranscribed episode in a folder at once with standard
  parameters. A video that fails to queue stays checked instead of being
  silently dropped.
- **Series** (`/series`) — per-series glossary: promote auto-mined name
  candidates to protected entities, edit or delete them.
- **Translate Subtitle** (`/translate`) — Workflow B. **Batch translate**
  mode translates every episode in a folder that already has an
  original-language subtitle beside it (`<stem>.<lang>.srt`) and no English
  one yet, in one click. It only auto-picks the source when there is exactly
  one such subtitle: an episode with several (which language is the
  dialogue?), none, or an English subtitle already is skipped, with the
  reason shown on its disabled checkbox. By default existing files are kept;
  tick **Replace existing English subtitles** to re-translate episodes that
  already have one (their `.en.srt` is overwritten; the source subtitle is
  never touched). **Upload from this computer** adds subtitle files from your
  PC to the batch: each is matched to an episode by its number (`S01E03`,
  `E03`, `3. Bölüm`, ...), you can change any match from its dropdown, and an
  uploaded file wins over a library subtitle for the same episode. Uploads
  (here and in the single-episode form) may be `.srt` or `.vtt`; a WebVTT
  file is converted on read, including one that is merely *named* `.srt`
  (common for streaming-service releases), and the saved original-language
  subtitle is always a real SRT. A job that fails to queue stays ticked /
  listed with the reason shown.
- **Jobs** (`/jobs`, `/jobs/:id`) — the queue and per-job detail: live
  stage, progress bar and ETA, QC findings, log, and cancel / retry /
  delete.

**Live progress.** A job's `stage` and `progress` advance through the real
pipeline milestones (e.g. *Transcribing* → *Translating* → *Writing
output*), with ASR and translation interpolating smoothly within their
share of the bar. The percentages are a directional heuristic (ASR is the
dominant cost of a video job), not a wall-clock promise; the ETA is a
linear extrapolation and is not shown below 2%.

**Reviewing and fixing subtitles.** A completed job has an *Edit
subtitles* panel that edits the target `.en.srt` in place: text only
(timing and cue count can't be changed), written atomically, without
re-running any pipeline stage or changing the job record. Each cue's
original line breaks are preserved. Cues flagged by the timing,
readability and output QC stages sort to the top. Translation, entity and
hallucination findings are indexed by sentence or ASR segment rather than
by final subtitle cue, so they can't be pointed at one cue and are not
highlighted there; the job's `needs_review` count still includes them.

## Pipeline

Workflow A (video transcription) moves through the following stages:

1. **Audio inspection** — probe the container for available audio streams
   and their properties.
2. **Stream selection** — pick the correct source audio track (e.g. the
   original-language track over a dub) automatically or by user choice.
3. **Language detection** — identify the spoken language of the selected
   stream.
4. **ASR (automatic speech recognition)** — transcribe speech to
   timestamped text.
5. **Hallucination detection** — flag and suppress ASR output that isn't
   grounded in actual audio (a known failure mode of Whisper-family
   models on silence, music, and noise).
6. **Translation** — translate the transcript into the target language,
   one sentence at a time (NLLB silently drops everything after the first
   sentence of a multi-sentence input). Two bounded refinements guard
   against known failure modes: a long unpunctuated run-on is retried in
   fixed-size word chunks and the chunked result is kept only when it
   preserves more protected entities or avoids severe truncation; and a
   single-word cue isolated by a real acoustic pause (e.g. one word of a
   sung line) is re-translated with its neighbouring cue as grounding
   context, replacing the context-free result only on an exact-match
   extraction (segmentation is never changed; see "Tuning knobs" below).
   The second applies to Workflow A only, since an uploaded `.srt` carries
   no acoustic-gap information.
7. **Target segmentation** — re-split translated text into subtitle-sized
   lines appropriate for the target language (translated text rarely
   maps 1:1 onto the source segmentation).
8. **Timestamp projection** — map timing from the source segments onto
   the newly segmented target lines.
9. **QC (quality control)** — automated checks across transcription,
   translation, entity handling, timing, segmentation, readability, and
   output correctness, with per-category flagging.
10. **Safe atomic output** — write the final subtitle file atomically, so
    a failed or interrupted job never leaves a partial/corrupt file in
    place of a previous good one.

## Architecture highlights

- **ASR**: [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  running `large-v3`.
- **Translation**: NLLB-200 (1.3B). Any supported source language is
  translated to **English**; English is the only output language (see
  "Output language" below).
- **Entity protection**: named entities (character names, places) are
  identified and protected through translation so they aren't mangled or
  inconsistently rendered across a series.
- **Stream-aware caching**: intermediate artifacts are cached per audio
  stream/config, so re-running a job with a different setting doesn't
  repeat expensive ASR work unnecessarily.
- **Job system**: jobs are tracked persistently (SQLite-backed job store)
  with status, history, and recovery across restarts.
- **GPU lock**: GPU-heavy stages (model load through inference) are
  serialized across all processes sharing the same GPU, preventing
  concurrent jobs from exhausting VRAM on single-GPU deployments.
- **VRAM pre-flight check**: the GPU lock only serializes *this project's*
  processes, so it can't see another program on the same card (a Tdarr,
  Jellyfin or Plex transcode burst). Before every in-process CUDA model
  load (ASR, local NLLB, the remote translate-server's NLLB, the stream
  sampler) the app polls free VRAM for up to 20 s and, if the required
  headroom never appears, fails the job with `InsufficientVramError`
  rather than attempting a load that would likely CUDA-OOM. Default margin
  is 3.2 GB (the sampler's lightweight model needs only 0.5 GB).
- **Lightweight stream sampling**: the library's "Analyze" button (spoken
  language per audio track) uses a small Whisper model (`small`, roughly
  200 MB of VRAM) instead of `large-v3` (roughly 3 GB), so an Analyze click
  can't crowd out a running job. If the small model isn't cached it falls
  back to `large-v3` with a logged warning.

## Output language

Output is always English: the translation model is pinned to English
output, and both workflows always write `<video stem>.en.srt`. Both
job-creation endpoints (`POST /api/jobs`, `POST /api/srt-translations`)
accept `target_lang` only as `"en"` (the default) and reject anything else
with HTTP 422 (`target_lang must be "en"; multi-target translation is not
supported`). The field is still stored on job records for compatibility.

## Status

Production-deployed, running on a single NVIDIA Tesla P4 GPU (Pascal,
`int8` compute -- this card has no efficient `float16` tensor
throughput; see `asr.AsrConfig.compute_type`'s docstring). Translation
(NLLB) runs on GPU, serialized against ASR via the same per-job GPU lock
(see `translate.TranslationConfig.device`'s docstring for the real
VRAM-headroom analysis behind this and the safety margin tuned into
`num_beams`/`batch_size`) -- this does mean real, ongoing GPU contention
with any other process sharing the card (e.g. a hardware-transcode tool),
which is not eliminated (the VRAM pre-flight check above bounds the damage
but doesn't remove the contention). Translation can optionally be
offloaded to a second GPU host (see "Remote translate-server"). Run the
tests (see "Development" below) rather than trusting a hardcoded number
here, since it drifts with every change.

## Running it

The project ships as a Docker Compose service with GPU passthrough:

```bash
docker compose up -d
```

The container expects the following volumes to be provided (see
`compose.yml`):

- a media directory mounted read-write, for input video and output
  subtitle files
- a models directory, for cached faster-whisper/NLLB model weights
- a cache directory, for the job database and intermediate work files
- a glossary directory (read-only), for per-series entity/name overrides

GPU access requires the NVIDIA Container Toolkit configured on the host.

Configuration is supplied via environment variables in `compose.yml`
(see that file for the current set, including optional TVDB metadata
lookup credentials).

### ASR decoding defaults

Chosen by measuring word recall against a human-made SRT for Love Is In
The Air S01E01 (two 20-minute windows; `asr.py` module docstring has the
table):

- **Hotwords are off** (`SUBTITLE_AI_ASR_HOTWORDS=on` restores them). The
  glossary/auto-mined list made Whisper output Title Case and dropped
  audio windows; character-name recall is ~3-5 points lower without it.
- **VAD is more permissive** than faster-whisper's default (onset 0.3,
  offset 0.15, 1 s minimum silence, 500 ms pad), worth +1.3 to +4.1
  points of recall.

Both are part of the transcript cache identity, so existing cached
transcripts are not reused after the change.

### Scratch work directories

Each job's intermediate files (extracted audio, stream samples,
pre-commit subtitles) live in `SUBTITLE_AI_WORK_ROOT/<job_id>` (default
`/cache/work`). They are managed automatically:

| Job outcome | What happens to its scratch directory |
|---|---|
| Completed, cancelled | Removed as soon as the job finishes (final subtitles are already committed to the media library) |
| Failed, validation failure | Kept for diagnosis for `SUBTITLE_AI_FAILED_WORK_RETENTION_HOURS` (default `24`), then removed |
| Job deleted via the API | Removed immediately |

A sweep at startup and then hourly removes anything past its window,
including directories with no matching job. It never touches queued or
running jobs, and only ever removes direct child directories of the work
root -- never the transcript cache, job database, or SRT upload staging.
Cleanup problems are logged and never change a job's result.

### Tuning knobs

| Variable | Default | Effect |
|---|---|---|
| `SUBTITLE_AI_API_KEY` | unset (open; intended for a LAN) | When set, mutating endpoints (cancel, retry, delete, glossary writes, subtitle edits) require a matching `X-API-Key` header. Job creation and read endpoints stay open. |
| `SUBTITLE_AI_FAILED_WORK_RETENTION_HOURS` | `24` | How long a failed job's scratch directory is kept (see below). |
| `SUBTITLE_AI_ASR_HOTWORDS` | off | `on` feeds glossary names to Whisper as hotwords (see "ASR decoding defaults"). |
| `FAILURE_WEBHOOK_URL` | unset | A plain JSON POST is sent to this URL when a job fails (works with anything that accepts one, or a relay in front of it). |
| `TVDB_API_KEY`, `TVDB_API_PIN` | unset | Optional TheTVDB series-title enrichment; the app never depends on it. |
| `SUBTITLE_AI_SAMPLE_MODEL` | `small` | Whisper model the library's "Analyze" button uses. |
| `SUBTITLE_AI_VRAM_MARGIN_GB` | `3.2` | Free VRAM the pre-flight check waits for before loading a large model. Must be a number. |
| `SUBTITLE_AI_ORPHAN_CONTEXT_PADDING` | on | `off`, `0`, `false` or `no` disables the isolated-word grounding pass. |
| `TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS` | `120` | Remote translate-server only: idle seconds before its NLLB model is unloaded (see "Remote translate-server"). |
| `TRANSLATE_SERVER_DEFAULT_LANG` | `tr` | Remote translate-server only: source language warmed at boot so the first request doesn't pay model-load latency. |

All of these are forwarded from `.env` (see `.env.example`): by `compose.yml`
for the main app, and by `compose.translate-server.yml` for the remote host
(`TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS`, `TRANSLATE_SERVER_DEFAULT_LANG`, plus
`SUBTITLE_AI_VRAM_MARGIN_GB`, which both hosts honour). An empty or unset
value means the default, and invalid values (a non-numeric or negative
number, or a language code the translation model doesn't know) fall back to
the default with a logged warning rather than failing to start or silently
disabling the feature.

To pre-cache the `small` sampler model once on the host (the app's `/models`
mount is read-only, so it can't download it itself), run this from a Python
environment that has `faster-whisper`, pointing `CONFIG_PATH` at the same
appdata directory `compose.yml` mounts (or set `MODELS_DIR` directly):

```bash
CONFIG_PATH=/path/to/appdata python scripts/download_sample_model.py
```

### Database backups

`scripts/backup_jobs_db.sh [source_db] [backup_dir]` takes a safe online
backup of the SQLite job store (sqlite3's `.backup`, fine against the live
WAL database; no need to stop the container), gzips it, and prunes backups
older than 14 days. Its default paths match one specific deployment, so
pass both arguments for yours. No schedule is installed automatically; the
script's header has a suggested cron line.

### Remote translate-server (optional)

`translate_server.py` can run on a separate GPU host (`TRANSLATE_SERVER_URL`
on the main app; deployed with `scripts/deploy.sh`) and the main app falls
back to local translation if it is unreachable. It keeps **exactly one**
NLLB model resident regardless of how many source languages it serves (a
further language costs only a small tokenizer), runs one translation at a
time, and unloads the model after `TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS`
(default `120`) with no activity so a shared GPU is freed between jobs.
It never unloads while a request is running or queued, and applies the same
VRAM pre-flight check before loading the model (that GPU is typically shared
with a media server's hardware transcoding). An unsupported `src_lang`
returns HTTP 422; `GET /health` reports `model_loaded`.

## Development

Backend tests (the `uv run` form works from a fresh shell; see `.github/
workflows/test.yml` for the exact CPU-only CI setup):

```bash
PYTHONPATH=subtitle_ai uv run --with pytest pytest tests -q
```

Frontend (React + Vite + Vitest, in `frontend/`):

```bash
cd frontend && npx tsc --noEmit && npm test -- --run
```

Deploying (`scripts/deploy.sh`) builds the image, redeploys the main app,
ships the same image to the translate-server host, and health-checks both;
set `SKIP_REMOTE=1` for changes that don't touch `translate.py` or
`translate_server.py`. `CLAUDE.md` holds deploy and operations notes;
`IMPROVEMENT_PLAN.md` tracks the roadmap and what has shipped.
