# Test report

Environment: Ubuntu 26.04 host (RTX 3070 8 GB, not used for these runs). Every run below happened in a throw-away
container with no GPU (`NVIDIA_VISIBLE_DEVICES=void`), repo mounted read-only and copied to a scratch directory.
Python 3.12 slim (Debian bookworm, ffmpeg 5.1.9), CPU torch 2.5.1, dependencies from `uv.lock` as in CI.
Date: 2026-11-18.

## Baseline (commit 57f9d48, before any change)

| Layer | Command | Result |
|---|---|---|
| Lint | `ruff check subtitle_ai scripts tests` | passed |
| Types | `mypy` (53 files) | passed |
| Backend | `unittest discover` | 1436 run, OK, 2 skipped (45 s) |
| Frontend types | `tsc --noEmit` | passed |
| Frontend tests | `vitest` | 108 passed, 14 files |
| Frontend build | `vite build` | passed |
| Advisories | `npm audit --omit=dev` | 2 moderate (react-router), not fixed |

## After change F-01 (atomic audio extraction)

| Layer | Result |
|---|---|
| Lint | passed |
| Types | passed (53 files) |
| Backend | **1441 run, OK, 2 skipped** (5 new tests, 43 s) |
| Real ffmpeg | synthetic two-track MKV: stream 1 extracted to 1 channel, 16000 Hz, 3.04 s; only `ep.wav` left in the directory |
| Real ffmpeg, bad stream index 9 | `MediaError`, no partial file left |
| Real ffmpeg, missing input | `MediaError`, no partial file left |

## After change F-10 (SSE reconnect)

| Check | Result |
|---|---|
| New tests on the **old** hook | reconnect-after-503 test failed (defect reproduced); the 401 test passed |
| New tests on the fixed hook | 3 of 3 pass in `useEventStream.test.tsx` |
| Frontend typecheck | passed |
| Frontend tests | **110 passed**, 14 files (was 108) |
| Frontend build | passed |

## Segmentation fuzz (read-only analysis, nothing changed)

20,000 random texts with random time windows through `segmentation_target.segment`: no exceptions, no empty output,
no overlapping cues, no zero or negative durations, no cue outside its window, no lost, duplicated or reordered words,
no cue over 2 lines. 734 cases (3.7%) had a line over 42 characters (up to 49); the same check on 2,276 cues built from
real library English dialogue found 6 (0.26%, worst 44). See F-11. This is a property check on synthetic text, not a
quality measurement.

## Deployment verification (2026-11-18, RTX 3070, container `subtitle-ai`)

All of these were executed against the running container. Test media was a 3-minute Turkish clip (one Opus audio
track) cut from episode S01E01 into a scratch library; the real library was not written to.

| Check | Result |
|---|---|
| CUDA image build | succeeded, 10.4 GB |
| Model provisioning | large-v3, small, NLLB 1.3B, opus-mt tr-en downloaded; NLLB converted to CTranslate2 float16 |
| Container health | `healthy`, `/api/health` ok, worker alive, DB ok |
| Auth | no key 401, wrong key 401, correct key 200, POST without key 401 |
| Frontend | `/` and a hashed `/assets/*.js` served (HTTP 200); **not opened in a browser** |
| Path traversal | `/assets/../../etc/passwd` 404; browse `../etc`, `../scripts` 400 |
| GPU in container | `torch.cuda.is_available()` True (RTX 3070), CTranslate2 sees 1 CUDA device |
| Mounts | `/models` read-only, `/cache` and `/glossary` writable, uid 1000 |
| Workflow A (video) | completed in 42 s from queue (cold models): language detected `tr`, `.tr.srt` and `.en.srt` written, `config_snapshot` present, QC counts recorded, log lines tagged `[job=...]`; peak VRAM 4255 MiB, GPU util max 98 %, max 58 C, max 154 W (3-minute clip, not a benchmark) |
| Workflow B (upload) | existing `.tr.srt` and `.en.srt` kept (hashes unchanged) with both overwrite flags off; with only `overwrite_english` on, only `.en.srt` changed and `.tr.srt` stayed identical; zero Whisper log lines for those jobs |
| Cancel queued job | cancelled, second job for the same video rejected 409 |
| Cancel running job | cancelled 9 s after the request, during transcription |
| Unwritable destination | job failed `OUTPUT_ERROR`, existing `.en.srt` unchanged, no temp files left |
| Retry | retry of the failed job created a new job linked to the original and completed |
| Restart mid-job | all job records kept, interrupted job requeued (1/3) and completed |
| Backup | online backup written, restored copy passed `integrity_check`, 10 rows |
| Library mount | `/data/media` visible, show folders writable by uid 1000 (read-only `-w` test on 1,230 folders) |

## Not executed

A full-length episode through the deployed container and any benchmark or optimisation run; the VRAM pre-flight
failure path; shared-GPU mode; a second concurrent GPU consumer; the remote translate server; Sonarr, Radarr, TVDB,
TMDB, Plex, Jellyfin and webhook integrations; VTT upload, the batch UI and the subtitle editor; multi-audio-track
selection on a real file; mixed-language audio; the browser UI. None of these is reported as passed. The 2 skipped
backend tests were not examined.

## Known limitations of this report

The tests mock model inference, so a green suite says nothing about transcription or translation quality.
The real-ffmpeg check used synthetic audio, not a real episode.
