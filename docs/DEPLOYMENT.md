# Deployment Guide

## Requirements

- Docker Engine + the `docker compose` plugin (v2).
- For NVIDIA acceleration: the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  installed on the host (`nvidia-container-toolkit`), and a working NVIDIA driver.
- For AMD acceleration: a host with the amdgpu/ROCm kernel driver. **This path is
  documented but not verified on real ROCm hardware** — the development host had only an
  NVIDIA GPU. Treat it as a starting point, not a guarantee.
- No accelerator: everything still works, just slower (see "CPU-only" below). Hardware is
  auto-detected at container startup — you don't need to tell it what's available.

## Quick start

```bash
git clone <repo> && cd Self-Hosted-AI-Subtitle-Platform
cp .env.example .env   # edit as needed — all values have sane defaults

# CPU-only (works everywhere):
docker compose up --build -d

# NVIDIA GPU:
docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up --build -d

# AMD ROCm (best-effort, unverified):
docker compose -f docker-compose.yml -f docker-compose.rocm.yml up --build -d
```

Then open http://localhost:8080 for the UI. The single container exposes nginx on port
8080; FastAPI listens only on the internal container loopback interface.

Mount `/data` as the media-only mount and `/config` as the application-state mount. The
application uses `/data/media` for source media and `/config/subtitleai/{db,output,models,work}`
for its database, generated files, model cache, and temporary processing files. Override
the host paths with `MEDIA_DIR` and `CONFIG_DIR` in `.env` when required. Because the
container runs as UID/GID 1000, the host directories must be writable by that account
(for example, `chown -R 1000:1000 /config/subtitleai` after creating the directory).

Check it's actually up:

```bash
curl http://localhost:8080/api/health        # {"status": "ok"}
curl http://localhost:8080/api/hardware      # confirms what was detected inside the container
```

The first job run downloads model weights (faster-whisper + NLLB-200) into
`/config/subtitleai/models` — this can take several minutes and needs outbound network
access to Hugging Face on first use only; subsequent jobs reuse the cached weights there.

## What each override file changes

| File | Effect |
|---|---|
| `docker-compose.yml` | Single-container base stack: nginx, FastAPI, and in-process workers with the CPU torch wheel. |
| `docker-compose.nvidia.yml` | Rebuilds the combined image with the CUDA torch wheel and adds an NVIDIA GPU device reservation (`count: all`). |
| `docker-compose.rocm.yml` | Rebuilds the combined image with the ROCm torch wheel and passes through `/dev/kfd` + `/dev/dri` plus `video`/`render` group membership. **Unverified.** |

Only the combined `subtitleai` image build changes between profiles; the frontend and
backend remain in the same container for every profile.

## Environment variables (`.env`)

See `.env.example` for the full list with defaults. Notable ones:

- `WHISPER_MODEL_SIZE` — requested faster-whisper model size (`tiny`…`large-v3`, default
  `large-v3` — the highest-quality option, validated in production by a sibling project).
  Automatically downgraded per-job if the detected hardware can't fit it (see
  `app/hardware.py::recommended_whisper_model` — CPU-only caps at `small`; GPU tiers scale
  with detected VRAM). Set to `small` or `medium` directly on constrained hardware for
  faster, lighter jobs rather than relying on the auto-downgrade every time.
- `SUBTITLE_NLLB_MODEL_NAME` (set directly, not in `.env.example`) — NLLB checkpoint,
  default `facebook/nllb-200-distilled-1.3B`. This is roughly 4x the size of the
  `distilled-600M` checkpoint (both real-world sibling projects independently converged on
  and validated 1.3B in production for translation quality) — budget accordingly (see
  resource sizing below). Override to `facebook/nllb-200-distilled-600M` on constrained
  hosts; both are self-hosted, no API key needed either way.
- `OPENAI_WHISPER_API_KEY` / `TRANSLATION_API_KEY` — optional cloud fallback engines. Leave
  blank to run fully offline; the fallback chain just logs them as unavailable and skips.
- `TVDB_API_KEY` / `TVDB_API_PIN` (set directly, not in `.env.example`) — optional, for
  auto-populating a translation glossary from a TVDB series' cast list
  (`Job.tvdb_id`) so character names survive translation without hand-typing a glossary.
  Entirely best-effort: unset means jobs proceed with only manually-supplied glossary
  entries (`Job.glossary_entities`), never fail. Get a free key at thetvdb.com.
- `WORKER_THREADS` — number of in-process background threads that claim and run jobs
  (default 1). GPU-bound stages are still serialized correctly across however many threads
  you run via the DB-backed GPU slot semaphore (`SUBTITLE_MAX_CONCURRENT_GPU_JOBS`,
  configured in `app/config.py`, default 1 slot per GPU) — raising `WORKER_THREADS` mainly
  helps when jobs spend time in CPU-only stages (QC, formatting) or are waiting on I/O.
- `API_CPU_LIMIT` / `API_MEM_LIMIT` — hard resource limits on the `api` container (both the
  modern `deploy.resources.limits` and the classic `mem_limit`/`cpus` fields are set, for
  compatibility across Compose versions). The **default** `large-v3` Whisper + NLLB-200-
  distilled-**1.3B** combination needs meaningfully more than the base 8GB default — budget
  at least 12–16GB RAM for CPU-only, or raise this further if running both models loaded
  concurrently. Drop to `medium`/`small` Whisper and/or `distilled-600M` NLLB (see above) to
  comfortably fit the original 8GB default instead.

## Persistent volumes

| Volume | Contents | Notes |
|---|---|---|
| `/data` | Uploaded/source media only (read-only from the pipeline's perspective — never modified in place) | Media mount |
| `/config/subtitleai/output` | Generated `.srt`/`.vtt`/`.webvtt`/burned-in files + provenance sidecars | Persisted application state |
| `/config/subtitleai/db` | `subtitles.db` — the SQLite job/state store | Back this up |
| `/config/subtitleai/models` | Downloaded Whisper/NLLB weights and TVDB cache | Safe to delete to reclaim space; re-downloads on next use |
| `/config/subtitleai/work` | Per-job scratch space (extracted WAVs, intermediate JSON) | Safe to delete when no job is running |

## Mixed-hardware / multi-node notes

- The default deployment is single-node: one `api` container running both the HTTP API and
  in-process worker threads. `SUBTITLE_WORKER_THREADS` and the GPU slot semaphore keep this
  safe under concurrency (see `docs/ARCHITECTURE.md`'s queue section).
- To scale out workers onto separate hosts with different hardware later: the `gpu_slots`
  table and `jobs` table are the only coordination points, and both use atomic
  UPDATE-WHERE claims — SQLite in WAL mode handles this correctly for multiple processes on
  one filesystem, but **does not** support multiple hosts without a shared/networked
  filesystem for the DB file, which is out of scope for this default deployment. A
  multi-host deployment would need to swap SQLite for a networked database (e.g.
  PostgreSQL) — the `db/session.py` engine setup is the only place that would need to
  change; the queue logic in `jobs/queue.py` already uses portable SQL.
- Each worker process detects its own hardware independently at `JobRunner` construction
  time (`app/hardware.py::detect_hardware()`), so a future per-host worker deployment on
  mixed hardware (some GPU hosts, some CPU-only) would auto-adapt without configuration —
  this isn't wired up as separate Compose services today, but nothing in the pipeline
  stages assumes a specific hardware profile.

## CPU-only fallback, verified

Hardware detection was verified in this build on a host **with** an NVIDIA GPU by
deliberately running the CPU-only Compose profile (which doesn't pass through the GPU
device) — `GET /api/hardware` correctly reported `{"vendor": "cpu", "fallback_reason":
"No NVIDIA CUDA or AMD ROCm device detected; running CPU-only."}` from inside the
container, and a full pipeline job completed successfully on CPU with the Whisper model
size auto-downgraded from the configured `medium` to `small`.

## Troubleshooting

- **`docker compose build` fails installing `openai-whisper`**: this is a known upstream
  issue where `openai-whisper`'s `setup.py` needs `pkg_resources` at build time, which
  recent `setuptools` no longer guarantees inside pip's isolated build environment. The
  Dockerfile already pins `setuptools<81` and uses `--no-build-isolation` to work around
  this — if you're building outside Docker, do the same:
  `pip install "setuptools<81" && pip install --no-build-isolation openai-whisper==20240930`.
- **Job stuck in `queued` forever**: check `docker compose logs subtitleai` for worker thread
  errors, and confirm `WORKER_THREADS >= 1`. If GPU-bound, confirm `gpu_slots` was
  initialized (`GET /api/hardware` should show `gpu_count > 0`) — a job waits up to 30
  minutes for a free GPU slot (`JobRunner.GPU_SLOT_WAIT_TIMEOUT_S`) before failing loudly
  rather than hanging forever.
- **All ASR engines unavailable**: the job's `error_message` and `/api/jobs/{id}/logs`
  stage log both record every engine's skip/failure reason — check there first. The most
  common cause is a `whisper_cpp`/`vosk` model path that was never provisioned (both are
  optional fallbacks, disabled by default until you supply model files under
  `/config/subtitleai/models`).
- **Translation quality on idioms**: NLLB-200-distilled-600M is a small, fast model and can
  translate idioms too literally (e.g. "smoke test" → literal "smoke" + "test" in the
  target language rather than the idiomatic equivalent). Swap `nllb_model_name` to a larger
  NLLB checkpoint, or configure the optional API translation engine, for higher fidelity at
  the cost of speed/self-hosting.
