# Initial audit

Audit date: 2026-11-18. Commit audited: `57f9d48` (master, "Translate Turkish with a dedicated opus-mt model").
Audit host: Ubuntu 26.04, RTX 3070 8 GB, driver 580.178.04, Docker 29.8.2, Compose v5.5.1.

## 0. Scope and honesty statement

This is the **Phase 1** audit. It records what was actually run and read, and marks everything else
as *documented, not independently verified*. Nothing in this file is a claim of success for a step
that was not executed.

**Executed (CPU only, in throw-away containers that mount the repo read-only):**

| Check | Result |
|---|---|
| `ruff check subtitle_ai scripts tests` (0.16.10, repo config) | pass |
| `mypy` (2.4.0, 53 source files, no exclusions) | pass |
| Backend `unittest discover` | **1436 tests, OK, 2 skipped**, 45 s |
| Frontend `tsc --noEmit` | pass |
| Frontend `npm test` (vitest) | **108 passed**, 14 files |
| Frontend `npm run build` | pass (vite 6.4.3) |
| `npm audit --omit=dev` | 2 moderate (react-router / react-router-dom) |
| Dependency advisories for the 69 packages in `uv.lock` (OSV) | 8 packages with advisories, see 13 |
| Secret scan of the working tree and full git history | no matches |

**Not executed:** GPU tests, Docker image build of the CUDA image, `docker compose up`, end-to-end jobs,
benchmarks, Sonarr/Radarr/TVDB/TMDB/Jellyfin/Plex integration tests, restart-recovery tests. The 2 skipped
backend tests were not investigated.

**Read in full or in the relevant part:** `api.py` (auth, upload, health, static routes, path handling),
`output.py` path guards, `media.py`, `gpu.py` (outline and lock), `worker.py` (cancel path), `jobstore.py`
(claim and WAL), `pipeline.py` (first 260 lines plus extraction call sites), `asr.py` (header and config),
`hallucination.py` (header), `translate.py` (config), `startup_guard.py`, `Dockerfile`, `compose.yml`
(environment block), `.github/workflows/test.yml`, `CLAUDE.md`, README sections.
Second pass added: `worker.py` SRT path and cancel/retry/recovery in `jobstore.py`, `srt_translation.py` parsing and
output, `media_servers.py` / `alerting.py` failure handling, `segmentation_target.py` (fuzzed), the frontend API
client and SSE hook.
**Still not read:** the `qc/` modules, `projection.py`, `segmentation_source.py`, `glossary*`, `cast_*`,
`name_correction.py`, `retime*.py`, `turns.py`, `arr_client.py`, `tvdb_client.py`, and most frontend pages.
Findings about those areas are deferred, not cleared.

## 1. Existing architecture

One container (`subtitle-ai`, port 8099 to 8080) running FastAPI/uvicorn, a single background worker thread,
an SQLite (WAL) job store, an in-process event bus with SSE to the React UI, and a file-based GPU lock.
Two independent workflows share the translation engine:

- **Workflow A (video):** probe, stream selection, language ID, faster-whisper large-v3, hallucination scoring,
  sentence-level NLLB translation, target segmentation, timestamp projection, QC, atomic SRT write.
- **Workflow B (subtitle):** SRT/VTT parse, shared translation, QC, atomic write. No ASR import.

An optional second container (`compose.translate-server.yml`, port 8091) serves translation from a separate GPU host.

## 2. Component responsibilities (from module docstrings; read for those listed in section 0)

`api.py` REST+SSE, auth, path resolution, static SPA. `worker.py` job execution and cancellation.
`jobstore.py` durable state. `pipeline.py` Workflow A orchestration. `media.py` / `audio_streams.py` probe,
extract, stream ranking. `asr.py` Whisper. `hallucination.py` scoring plus signature registry.
`translate.py` NLLB (ct2 or hf) and the dedicated Turkish model. `gpu.py` lock, residency, VRAM pre-flight.
`output.py` atomic writes and path guards. `qc/*` advisory checks.

## 3. Data flow

Video, then ffprobe, then ffmpeg to `<work>/<stem>.wav` (mono 16 kHz), then faster-whisper (word timestamps),
then canonical transcript (cached under `/cache/transcripts`), then hallucination filter, then NLLB per sentence,
then target segmentation and projection, then QC, then `output.write_srt_atomic` next to the video.

## 4. GPU execution flow

`gpu_lock()` (gpu.py:312) is a reentrant `flock` on `SUBTITLE_AI_GPU_LOCK` (`/cache/.gpu.lock` in compose).
Models stay resident after use and are evicted on request or idle timeout; a shared `.resident` flock lets another
process ask the holder to evict. `preflight_vram_check` (gpu.py:253) enforces the VRAM margin (default 3.2 GB).
Release is in a `finally` block (gpu.py:363). Behaviour under a killed holder, a cancelled job and a second process
was **not tested**.

## 5. Storage and persistence

Mounts: `/data` media, `/models` weights (intended read-only), `/cache` DB, transcripts, work and GPU lock,
`/glossary` series glossaries. Verified in code: defaults for the cache sub-directories (uploads, cast, arr, tvdb,
glossary suggestions) all sit under `/cache`, so no application state lands in `/data`.

## 6. Job lifecycle

Claims use `BEGIN IMMEDIATE` (jobstore.py:263), WAL is enabled (jobstore.py:255). Cancellation is **cooperative**:
`worker.py:575` checks `is_cancel_requested` each time the pipeline emits an event.

## 7. API endpoints and security

25 routes. Every `/api/*` route except `/api/health` carries `Depends(require_api_key)`; the SPA catch-all and
`/assets/*` are public by design. Verified:

- Key compared with `hmac.compare_digest` (api.py:82).
- With no key set, mutating requests are refused when `Sec-Fetch-Site` or `Origin` is cross-origin (api.py:94-99).
- `startup_guard.py` refuses a non-loopback bind without a key unless `SUBTITLE_AI_ALLOW_INSECURE` is set.
- `resolve_media_path` (output.py:45) resolves and re-checks containment; output names are restricted
  to `<stem>.<lang>.srt` and protected suffixes are refused (output.py:66).
- Uploads (api.py:576): extension allow-list, 2 MiB cap, UTF-8 check, server-chosen uuid filename, staged under `/cache`.
- `/assets/{filename:path}` resolves and checks the parent (api.py:1167). The SPA fallback never builds a path from input.
- ffmpeg/ffprobe are called with argument lists, never a shell, with timeouts. The video path is always the
  resolved absolute path, so it cannot be read as an option.
- No `shell=True`, `eval`, `exec`, pickle or unsafe YAML loads in `subtitle_ai/`.

## 8. Frontend structure

React + Vite + TypeScript, 14 test files / 108 tests, builds to `/app/static`. Pages: Library, Series, Translate
Subtitle, Jobs, Job detail, Retime. Source not yet audited line by line.

## 9. Docker deployment architecture

Node 22 build stage, then python:3.12-slim-bookworm, static ffmpeg copied from `mwader/static-ffmpeg`,
`uv sync --frozen`, non-root uid 1000, HEALTHCHECK on `/api/health`, NVIDIA runtime. Not built in this audit.

## 10. Existing integrations

Sonarr/Radarr (`arr_client.py`), TheTVDB, TMDB/IMDb cast metadata, Jellyfin/Plex refresh (`media_servers.py`),
failure webhooks (`alerting.py`), optional remote translate server. Not exercised.

## 11. Test coverage and gaps

1436 backend + 108 frontend tests pass. Gaps identified so far: no test for cancelling during ffmpeg extraction;
no test that a failed extraction leaves no partial WAV; no GPU-marked tests runnable in CI (by design, CI is CPU-only).

## 12. Known technical debt (from the repo's own `docs/handover.md`)

Malay and other non-Turkish hallucinations are not suppressed because `hallucination_signatures.json` holds only
Turkish signatures. An unresolved reported ~5.1 s human/AI offset (real pair missing). Sung lyrics are listed as
known, not fixed. Planning drafts (`ENHANCEMENT_DRAFT.md`, `IMPROVEMENT_PLAN.md`, `enhancement.md`) sit beside current docs.

## 13. Findings

| ID | Sev | Finding | Status |
|---|---|---|---|
| F-01 | P2 | `media.extract_audio` (media.py:57) writes straight to the final WAV path. If ffmpeg times out or is killed, a partial file stays; a later `if not wav_path.is_file()` check (pipeline.py:398) would treat it as valid. Also no `-nostdin`. | **Fixed** in `media.py` (temp file, `-f wav`, `-nostdin`, atomic rename, cleanup on failure) with 5 new tests in `tests/test_media_extract.py`; also checked against real ffmpeg, see `TEST_REPORT.md`. Behaviour change: an `OSError` during the rename is now reported as `MediaError`. |
| F-02 | P2 | Cancel is not honoured while ffmpeg runs (up to the 1800 s timeout in media.py:69) because the check runs only on pipeline events (worker.py:575). | Verified by reading; not yet fixed |
| F-03 | P2 | Dependency advisories (OSV, not filtered by applicability): `starlette` 0.46.2 (14), `python-multipart` 0.0.20 (14), `torch` 2.5.1 (23), `transformers` 4.48.3 (36), `filelock`, `certifi`, `sentencepiece`, `setuptools`. Exploitability in this app was **not** assessed. The two web-facing ones (starlette, python-multipart) handle the upload path. Upgrading torch/transformers/ctranslate2 is constrained by the repo's compatibility pins. | Open |
| F-04 | P2 | `npm audit` reports 2 moderate advisories in `react-router` / `react-router-dom` 6.x (SSR hydration; this app is client-only, so likely not reachable). Fix needs a major upgrade. | Open, low reachability |
| F-05 | P3 | 19 environment variables read by code are undocumented in `.env.example` and the docs (cue-duration/CPS/line limits, `SUBTITLE_AI_NLLB_CT2_PATH`, several `/cache` path overrides). See `ENVIRONMENT_VARIABLE_AUDIT.md`. | Open |
| F-06 | P3 | No LICENSE file on a public repository. | Open |
| F-07 | P3 | `/api/health` is unauthenticated and reports worker heartbeat age and model residency. Low sensitivity; needed by the container healthcheck. | Accepted |
| F-08 | P3 | A symlink inside the media tree that points outside it is rejected because paths are resolved before the containment check. Safe, but a library built from symlinks would not work. | Document |
| F-09 | P3 | The non-Turkish hallucination gap and stale planning docs in section 12. | Open |

Added in the second audit pass (worker lifecycle, Workflow B output, integrations, segmentation, frontend client):

| ID | Sev | Finding | Status |
|---|---|---|---|
| F-10 | P2 | **Live updates die permanently after a server restart.** `frontend/src/api/useEventStream.ts` returned silently on any non-OK `/api/events` response (`if (!response.ok ...) return;`), so a 502/503 while the container or proxy comes back meant no reconnect until the page was reloaded. Only network errors and clean stream ends reconnected. | **Fixed**: non-auth failures retry with 2 s doubling to 30 s; 401/403 stops without re-prompting. Reproduced first (test failed on the old code), then 3/3 pass. |
| F-11 | P3 | `segmentation_target.wrap_lines` can emit a line longer than `MAX_LINE_CHARS` (42) when a cue of up to 84 characters has no word boundary that fits two lines (fallback at `segmentation_target.py:142`). On real English dialogue from the library subtitle (2,276 cues) 6 cues (0.26%) were affected, worst 44 characters. Random stress text reached 49. No overlap, no lost or reordered words and no cue outside its source window in 20,000 random cases. | Documented, not changed: a segmentation change should be judged on a human reference, not synthetic text. |
| F-12 | P3 | Workflow B re-renders the original-language subtitle (`srt.render`) before committing it beside the video. Text and timestamps survive, but cues are renumbered and empty cues dropped (verified by round trip). If `overwrite_original` is ticked and the source is the library's own `<stem>.<lang>.srt`, that file is replaced by the normalised copy. Default is off. | Documented; opt-in only. |
| F-13 | P3 | `write_srt_atomic` checks `exists()` before writing (output.py), a check-then-act gap. Harmless with the single worker; relevant only if a second writer is ever added. | Documented |

Verified as sound in this pass: atomic write (temp file, fsync, `os.replace`, temp cleanup); crash recovery
requeues `running` jobs at startup with a finite retry budget; cancelling a queued job is one transaction before
`claim()`; retry creates a new row and inherits the overwrite flags; media-server refresh runs on its own thread with
timeouts and cannot fail a completed job; failure webhook errors are caught; Workflow B never imports ASR.

No P0 or P1 defect was confirmed in the areas read.

## 14. Deployment risks

- The documented hardware matches this host (RTX 3070 8 GB), but 354 MiB of VRAM was already in use at audit time
  with no compute process listed.
- `/opt/docker/appdata` and `/opt/docker/compose` exist and are empty; there is no running container to back up.
- The first start downloads or needs prepared model weights under `/models`; the `/models` mount is read-only.
- The image is about 10 GB per the repo's own CI note and was not built here.
- `/data` on this host is an NFS share from a NAS. Most media folders are world-writable, but at least one folder
  there is root-owned `drwxr-xr-x`, and deletes in it were refused from this host. The container runs as uid 1000, so
  output writes next to videos must be verified per library folder before relying on them.

## 15. Prioritised remediation plan

1. F-01 atomic audio extraction with `-nostdin`, plus a test. (Phase 2)
2. F-02 make extraction cancellable. (Phase 2, needs a small design decision)
3. F-03 triage starlette/python-multipart advisories against how the app uses them, then bump within the lockfile if
   the CUDA/CT2 pins allow. Do not touch torch/transformers/ctranslate2 without a GPU regression run.
4. Finish reading the unread modules in section 0 and add any findings here.
5. GPU-host verification: build the image, run a real job, measure VRAM and timings, restart recovery.
6. F-05, F-06 housekeeping.
