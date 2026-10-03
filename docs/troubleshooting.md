# Troubleshooting

Symptoms, causes and fixes, from what has actually gone wrong in this deployment.
Each cites where the detail lives.

## The container exits at start with "refusing to start"

The server is listening on a non-loopback address with no `SUBTITLE_AI_API_KEY`.
Set the key, or set `SUBTITLE_AI_ALLOW_INSECURE=1` in `.env` to accept an open API
on a trusted network, then recreate the container (`docs/deployment.md`).

## `/api/health` returns 503

`reasons` says which: `database unreachable` (the `/cache` mount, the file's
ownership by uid 1000, or a full disk) or `worker thread is not running` (the thread
died; check the log for `worker failed` lines and restart). `status: degraded` with
`worker heartbeat stale` and HTTP 200 means no pipeline event for over an hour: a
very long silent stage, or a wedged job (`docker logs`, then cancel it).

## A job failed: read `error_category`

| Code | Meaning | Fix |
|---|---|---|
| `LOW_CONFIDENCE_LANGUAGE` | Language detection was below the threshold and the policy requires a manual choice. | Retry with `source_lang` set. |
| `UNSUPPORTED_LANGUAGE` | No translation mapping for the language. | Pick a supported one (`GET /api/languages`). |
| `SRT_VALIDATION_ERROR` | The source subtitle is malformed. | Fix or replace it; the message names the cue. |
| `RETIME_REFUSED` | A re-timing job found too little shared text between the subtitle and the audio. | Check the subtitle belongs to this video and its language is right; nothing was written. |
| `OUTPUT_ERROR` | Unsafe path or a write failed; existing outputs untouched. | Check permissions and free space on the media mount. |
| `MEDIA_ERROR` | ffprobe/ffmpeg could not read the file or stream. | Play the file; try another audio stream. |
| `GPU_RESOURCE_ERROR` | Not enough free VRAM, or the GPU lock wait timed out. | Free GPU memory (other processes; a resident model); retry. See `docs/operations.md`. |
| `WORKER_ERROR` | An error escaped the job handler. | Read the log; this is a defect. |
| `PIPELINE_ERROR` | An unexpected error. | The job log has the traceback; this is a defect. |
| `ORPHANED_JOB_RECOVERY_EXHAUSTED` | The job was interrupted by restarts three times. | Retry once the host is stable. |

A failed job's scratch directory (WAV, partial SRTs) is kept for 24 hours
(`SUBTITLE_AI_FAILED_WORK_RETENTION_HOURS`) in `/cache/work/<job_id>`.

## CUDA out of memory

Two GPU consumers collided (another container, the Analyze sampler, a resident
NLLB). `SUBTITLE_AI_GPU_SHARED=1` on a shared card; the pre-flight check refuses a
load that would not fit and reports `GPU_RESOURCE_ERROR`. Do not remove the lock
(`docs/operations.md`).

## Wrong or missing text in a subtitle

- **"Altyazı M.K." / "Thanks for watching" style lines:** Whisper hallucinations.
  Turkish has registered signatures (`subtitle_ai/hallucination_signatures.json`);
  other languages do not. A Malay film showed ~31 such lines unsuppressed
  (2026-10-02, not yet addressed; `docs/handover.md`). Adding a signature needs
  evidence recorded per the file's convention.
- **A character name translated as an ordinary word ("Ateş" as "Fire"):** the
  series glossary lacks the name or the cast evidence was stale. Check the Series
  page's cast report; protection needs credited metadata, recurrence and a
  demonstrated failure (`docs/decisions/entity-protection-evidence-bar.md`).
- **A foreign-language scene translated as the main language:** code-switch
  detection needs a run of three confident clauses; short bursts are not overridden
  (`docs/architecture.md`, section 8).
- **A name misspelled by one letter:** `name_correction` fixes credited names that
  are one letter off, using the series' cached transcripts; a series with few
  processed episodes has a weaker veto (`docs/decisions/2026-10-02-audio-only-asr-inputs.md`).

## Subtitles appear at the wrong time

Do not add an offset. Measure: `python scripts/eval_srt_quality.py reference.srt
candidate.srt` reports `median_time_offset`, `window_median_range`, drift and a
`constant_offset` / `drift_or_nonlinear` verdict. A constant offset usually means
the reference was timed to a different cut; check the media's stream start times with
`ffprobe`. Full method and what is known: `docs/decisions/subtitle-timing.md`.

## The UI shows a 401 prompt, or live updates stop

The API key is set and the browser has none or a wrong one; enter it again (stored
in `localStorage`). Live updates use an authenticated `fetch` stream limited to 25
subscribers; a 429 means too many tabs. The UI refetches on every event; a stream
that is blocked by a proxy buffer needs `X-Accel-Buffering: no` honoured.

## A subtitle was not replaced

Overwrite flags default to keep: `overwrite_original` and `overwrite_english` are
independent, and a kept file is logged as `KEEP: <name> already exists`.

## CI is red

`lint` (ruff), `test` (mypy, backend tests) and `frontend` run separately; the failing
job's name says which. Both linters run with no exclusions; fix the cause. A docs
test fails when a route is missing from `docs/api.md` or an env var is missing from
`.env.example`, `compose.yml` or the README table.

## Where to look

`docker logs subtitle-ai` (each job line ends `[job=<id>]`; set
`SUBTITLE_AI_LOG_FORMAT=json` for machine-readable logs), `GET /api/jobs/<id>` (full
log, QC findings and `config_snapshot`), `/cache/work/<id>` for a failed job's files.
