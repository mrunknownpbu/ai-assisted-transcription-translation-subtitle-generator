# AI-Assisted Transcription & Translation Subtitle Generator

An automated pipeline that takes a video file with embedded or external
audio, transcribes the spoken dialogue, translates it, and produces a
timed, quality-checked subtitle file — with no manual transcription or
translation step.

It's built for long-form video (TV episodes, films) where subtitles either
don't exist in the target language or need to be regenerated, and is
tuned to avoid the two failure modes that make machine-generated
subtitles unusable in practice: hallucinated text and mistimed lines.

## Pipeline

Each job moves through the following stages:

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
- **Translation**: NLLB-200 (1.3B), covering a wide range of source/target
  language pairs from a single model.
- **Entity protection**: named entities (character names, places) are
  identified and protected through translation so they aren't mangled or
  inconsistently rendered across a series.
- **Stream-aware caching**: intermediate artifacts are cached per audio
  stream/config, so re-running a job with a different target language or
  setting doesn't repeat expensive ASR work unnecessarily.
- **Job system**: jobs are tracked persistently (SQLite-backed job store)
  with status, history, and recovery across restarts.
- **GPU lock**: GPU-heavy stages (model load through inference) are
  serialized across all processes sharing the same GPU, preventing
  concurrent jobs from exhausting VRAM on single-GPU deployments.

## Status

Production-deployed, running on a single NVIDIA Tesla P4 GPU (Pascal,
`int8` compute -- this card has no efficient `float16` tensor
throughput; see `asr.AsrConfig.compute_type`'s docstring). Translation
(NLLB) runs on CPU, not GPU, to avoid pinning the shared card during
that stage -- see `translate.TranslationConfig.device`'s docstring. Test
suite currently at 365 passing tests covering the pipeline stages, QC
categories, caching, job store, glossary loading, and API layer.

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

Run tests with:

```bash
pytest
```
