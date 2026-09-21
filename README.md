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
  `.srt` (e.g. a downloaded fansub) → translation → English subtitle. No
  ASR, no audio extraction -- the uploaded/selected `.srt` is already the
  transcription, and is never treated as though it came from ASR. Started
  from the "Translate Subtitle" page, it reuses the same translation
  engine, glossary, GPU lock, and job queue as Workflow A, but is a
  structurally separate pipeline (`srt_translation.py`).

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
6. **Translation** — translate the transcript into the target language.
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
which is not eliminated. Run `python -m pytest tests -q` (see `.github/
workflows/test.yml` for the exact CPU-only setup) rather than trusting a
hardcoded number here, since it drifts with every change.

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

### Remote translate-server (optional)

`translate_server.py` can run on a separate GPU host (`TRANSLATE_SERVER_URL`
on the main app; deployed with `scripts/deploy.sh`) and the main app falls
back to local translation if it is unreachable. It keeps **exactly one**
NLLB model resident regardless of how many source languages it serves (a
further language costs only a small tokenizer), runs one translation at a
time, and unloads the model after `TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS`
(default `120`) with no activity so a shared GPU is freed between jobs.
It never unloads while a request is running or queued. An unsupported
`src_lang` returns HTTP 422; `GET /health` reports `model_loaded`.

Run tests with:

```bash
pytest
```
