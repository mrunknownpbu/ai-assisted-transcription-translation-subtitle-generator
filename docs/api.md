# API reference

The contract between the web UI (or any client) and the backend. Source of
truth for behaviour is `subtitle_ai/api.py`; `tests/test_docs_consistency.py`
fails if a route exists there that is missing here.

## Conventions

- **Base path** `/api`. JSON in, JSON out. The React build is served by the same
  process (`/assets/*` and a client-side-routing fallback for every other
  non-`/api` path); an unknown `/api/*` path is a JSON 404, never HTML.
- **Authentication.** When `SUBTITLE_AI_API_KEY` is set, every `/api/*` route
  except `GET /api/health` requires an `X-API-Key` header equal to it (constant-
  time comparison) and answers `401 {"detail": "missing or invalid X-API-Key"}`
  otherwise. When it is unset, mutating requests (`POST/PUT/PATCH/DELETE`)
  are rejected with `403` if `Sec-Fetch-Site` is not `same-origin`/`none` or an
  `Origin` header differs from the request's own origin; headerless non-browser
  clients are accepted. The server refuses to start with no key on a
  non-loopback address unless `SUBTITLE_AI_ALLOW_INSECURE` is set
  (`docs/architecture.md`, "Security model").
- **Errors.** Failures are `{"detail": <string>}` with the status below, or
  FastAPI's `{"detail": [ {loc, msg, type} ]}` with `422` for a body or query
  that fails validation. Tracebacks are never returned.
- **Paths** in requests are relative to the media root; absolute paths, `..`
  escapes, symlinks leaving the root and protected files are rejected with `400`.
- **Pagination.** Only `GET /api/jobs`: `limit` (1-500, default 50) and
  `offset` (>= 0); the response carries `total`.
- **Idempotency.** Creating a job is not idempotent, but the store refuses a
  duplicate of a still-active job for the same target (`409`). `DELETE` of an
  absent job is `404`. Cancelling a terminal job is `409`.

## Job states

The stored `status` is one of `queued`, `running`, `completed`, `failed`,
`cancelled` (and `skipped`, which no code path currently sets). Every job also
carries `lifecycle`: `QUEUED`, `RUNNING`, `CANCELLING` (running with a cancel
requested), `CANCELLED`, `SUCCEEDED`, `FAILED`, `SKIPPED`. `stage` is the
pipeline stage name and `progress` is 0-100 (directional, not a promise).
`error_category` is a stable code on failed jobs (`docs/architecture.md`,
"Error model"). `config_snapshot` (detail only) records what the job ran with.

## Routes

### Health and discovery

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Liveness and readiness. `200` with `ok`, `status` (`ok`/`degraded`), `db_ok`, `worker_alive`, `worker_last_heartbeat_seconds_ago`, `nllb_resident`, and `reasons` when degraded. `503` (`ok: false`) when the database is unreachable or the worker thread has died. A heartbeat older than an hour is `degraded` but stays `200`. Never requires the key. |
| `GET /api/languages` | `{"languages": [...]}`: the source languages the translation model can handle (the keys of `translate.NLLB_LANG`). |
| `GET /api/queue` | Job counts by status (`QUEUED`, `RUNNING`, ..., `ALL`). |
| `GET /api/events` | Server-Sent Events stream, below. |

### Library

| Method and path | Purpose |
|---|---|
| `GET /api/browse?path=&file_type=video\|srt` | One directory level: sub-directories plus video (or `.srt`) files. |
| `GET /api/media?path=` | One video's detail: probe metadata plus `path`, `filename`, `size` and `existing_subtitles` (sibling `.srt` files). |
| `GET /api/audio-streams?path=` | Audio streams of a video with a language-ID recommendation. Samples audio on the GPU (bounded wait on the GPU lock; `503` + `Retry-After: 1` when a job owns the GPU). |

### Jobs

| Method and path | Request | Response |
|---|---|---|
| `POST /api/jobs` | `JobRequest`: `video_path` (required), `source_lang` (`auto` or a supported code), `target_lang` (must be `en`), `audio_stream_index`, `overwrite_original`, `overwrite_english` | `201 {"job": Job}` (Workflow A). `400` bad path, non-video or unsupported language; `409` duplicate active job. |
| `POST /api/srt-translations` | `SrtTranslationRequest`: `video_path` (required, gives the destination and series), exactly one of `source_srt_path` / `source_upload_id`, `source_lang`, `target_lang` (`en`), overwrite flags | `201 {"job": Job}` (Workflow B, no ASR). `400` bad path or not exactly one source; `409` duplicate. |
| `POST /api/srt-uploads` | multipart `file` (`.srt` or `.vtt`, UTF-8, <= 2 MiB) | `201 {"upload_id", "filename"}`; use `upload_id` as `source_upload_id`. `400` wrong extension or encoding, `413` too large, `503` uploads not configured. |
| `GET /api/jobs` | `status`, `limit`, `offset` | `{"jobs": [Job summary], "total": n}`. Summaries omit QC findings, the log and `config_snapshot` (kept small: a full page was measured at 6 MB). |
| `GET /api/jobs/{job_id}` | | The full `Job`: QC findings, log, `config_snapshot`. `404` if unknown. |
| `POST /api/jobs/{job_id}/cancel` | | Queued: cancelled immediately. Running: `cancel_requested` is set (`lifecycle` becomes `CANCELLING`) and the worker stops at its next checkpoint, leaving existing outputs untouched. `409` if already terminal, `404` if unknown. |
| `POST /api/jobs/{job_id}/retry` | `RetryRequest`: optional `overwrite_original`, `overwrite_english`, `source_lang`, `audio_stream_index`; omitted values inherit from the original | `201 {"job": Job}`: a NEW job (`retry_of_job_id`, `attempt + 1`); the original is never mutated. `409` if the original is not terminal. |
| `DELETE /api/jobs/{job_id}` | | Removes a terminal job's row and its scratch directory. `409` if active, `404` if unknown. |
| `GET /api/jobs/{job_id}/srt` | | The finished English subtitle as editable cues. |
| `PUT /api/jobs/{job_id}/srt` | `SrtEditRequest`: `edits: [{index, lines}]` | Text-only edit: timings, cue count and cue order are preserved; lines may not be empty or contain breaks or timing markers. Refused while the job is active. Written atomically. |

### Series and glossary

| Method and path | Purpose |
|---|---|
| `GET /api/series` | One summary per `tvdb_id` (plus an ungrouped row) with its title. |
| `GET /api/series/{tvdb_id}` | Jobs, the manual glossary, mined suggestions (for review only) and the cast-enrichment report. |
| `POST /api/series/{tvdb_id}/glossary/promote` | `{canonical, aliases}`: add a protected entity to the series glossary. |
| `POST /api/series/{tvdb_id}/glossary/update` | `{original_canonical, canonical, aliases}` |
| `POST /api/series/{tvdb_id}/glossary/delete` | `{canonical}` |

Glossary writes are serialised and round-trip the YAML (comments preserved) under
a file lock; the directory may be its own Git repository.

### Static

| Method and path | Purpose |
|---|---|
| `GET /assets/{filename}` | Built frontend assets. |
| `GET /{full_path}` | `index.html` for client-side routes. |

## Server-Sent Events (`GET /api/events`)

Each message is `data: {"type": "job_changed", "job_id": "<id>"}\n\n`. It is a
change signal only: a client refetches `GET /api/jobs` or `/api/jobs/{job_id}`
instead of trusting the event body, so response shape is decided in exactly one
place. Pending changes for the same job are coalesced; a keep-alive comment
(`: keepalive`) is sent every 15 s. At most 25 concurrent subscribers: the 26th
gets `429` with `Retry-After: 15`. The stream needs the key like any other route
(the UI uses an authenticated `fetch`, not `EventSource`, so the key is not in a
URL). The UI does not parse log text; stage and progress are fields on the job.

## Schemas

`JobRequest`, `SrtTranslationRequest`, `RetryRequest`, `SrtEditRequest` and the
glossary request models are the pydantic classes in `subtitle_ai/api.py`; the
generated OpenAPI document (`/openapi.json`, `/docs`) lists their fields and
constraints. The TypeScript shapes the UI consumes are in
`frontend/src/api/types.ts`.
