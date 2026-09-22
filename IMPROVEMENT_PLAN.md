# Subtitle AI: System Enhancement & Improvement Roadmap

This document consolidates the architectural findings, implemented enhancement phases, and prioritized roadmap recommendations for the Subtitle AI project.

---

## 1. Executive Overview & Goals

The Subtitle AI system transcribes and translates long-form media without manual intervention. Its architecture is tuned around two primary operational imperatives:
1. **Output Correctness & Pacing**: Eliminating hallucinated dialogue and mistimed/unreadable subtitle line splits.
2. **Resource & Memory Safety**: Preventing VRAM exhaustion on constrained hardware (Tesla P4 8GB shared with hardware transcoding) and bounding scratch disk accumulation.

---

## 2. Core Code-Review Findings & Problem Matrix

The initial architectural audit identified several high- and medium-severity issues across API contracts, VRAM allocation, and disk usage:

| Severity | Area | Finding | Root Cause / Evidence | Status |
|---|---|---|---|---|
| **High** | Target-language API contract | APIs accepted arbitrary 2–3 letter language codes, but NLLB was hardcoded to `eng_Latn`. Non-English requests produced English text under mislabeled filenames (`.fr.srt`). | `subtitle_ai/api.py`, `subtitle_ai/pipeline.py`, `subtitle_ai/translate.py` | **Resolved (Phase 1)** |
| **High** | Remote translation GPU memory | Remote translate-server loaded a full 1.3B NLLB model per source language (~2.8 GB each), exhausting remote GPU memory. | `subtitle_ai/translate_server.py:52-64` | **Resolved (Phase 2)** |
| **High** | Remote translation concurrency | Eviction timestamp was recorded before inference started; long translations were evicted mid-flight, allowing concurrent model loads. | `subtitle_ai/translate_server.py:52-83` | **Resolved (Phase 2)** |
| **Medium** | Scratch disk lifecycle | `WORK_ROOT/<job_id>` retained large intermediate WAV files indefinitely across all outcomes (success, failure, cancellation). | `subtitle_ai/worker.py`, `subtitle_ai/pipeline.py` | **Resolved (Phase 3)** |

---

## 3. Implemented Enhancements (Phases 1 – 4)

### Phase 1: Enforce English-Only Target Contract
* **Single Constant of Truth:** Added `TARGET_LANG = "en"` in `subtitle_ai/output.py`. Replaced hardcoded string literals across pipeline modules.
* **Pydantic Validation:** Added validation in `JobRequest` and `SrtTranslationRequest` rejecting non-`en` requests with HTTP 422 (`target_lang must be "en"; multi-target translation is not supported`).
* **Output Path Derivation:** Forced output filenames for video and SRT workflows to derive strictly from `TARGET_LANG` (`<stem>.en.srt`).
* **Backward Compatibility:** Legacy rows with non-English targets are coerced to `en` upon retry with a logged warning, preventing failures on historical job records.

### Phase 2: Single-Model, Concurrency-Safe Remote Translate Server
* **Single Resident Model:** Refactored `translate_server.py` state (`_state = {"config", "model", "tokenizers": {}, "bos", "last_used", "active_requests": 0}`). The model weights are loaded once; additional source languages load only lightweight tokenizers.
* **Lock Separation & Request Lifecycle:**
  * `_lock`: Guards server state and in-flight request counting (`active_requests`).
  * `_infer_lock`: Serializes model loading and inference to guarantee no concurrent forward passes.
* **Safe Idle Eviction:** Eviction checks both idle duration (`IDLE_UNLOAD_SECONDS=120`) and active request count (`active_requests == 0`). `last_used` updates in `finally` upon request completion.
* **Error Handling:** Unsupported source languages return HTTP 422 rather than uncaught 500 errors.

### Phase 3: Managed Scratch-Work Retention & Lifecycle
* **Workdir Module (`subtitle_ai/workdir.py`):**
  * `cleanup_work_dir(work_root, job_id)`: Enforces strict boundary checks (must be a direct child of `WORK_ROOT`, rejects symlinks, path traversals, or malformed UUIDs).
  * `sweep_stale()`: Periodically evaluates direct child directories against the SQLite job store.
* **Retention Policy:**
  * `completed` / `cancelled`: Workdir removed immediately upon final output commit.
  * `failed` / validation failure: Workdir retained for `SUBTITLE_AI_FAILED_WORK_RETENTION_HOURS` (default 24h) for inspection before deletion.
  * Job deletion via API: Associated workdir purged immediately.
* **Protected Paths:** Sweep explicitly ignores transcript cache, job database (`jobs.db`), and SRT upload staging directories.

### Phase 4: Operational Documentation
* Synchronized `README.md`, `CLAUDE.md`, `.env.example`, and Compose files to reflect the unified contracts, single-model remote server behavior, and scratch lifecycle.

---

## 4. Prioritized Improvement Roadmap & Future Proposals

The following recommendations are organized into actionable categories for ongoing enhancement.

```
                    ┌────────────────────────────────────────────────────────┐
                    │            Subtitle AI Improvement Roadmap             │
                    └───────────────────────────┬────────────────────────────┘
                                                │
         ┌───────────────────┬──────────────────┼──────────────────┬──────────────────┐
         │                   │                  │                  │                  │
         ▼                   ▼                  ▼                  ▼                  ▼
   1. Code Hygiene     2. GPU / VRAM      3. Subtitle Quality    4. Web UI / UX     5. LLM Horizons
   • Green CI Suite    • Whisper-small    • Run-on cue retry     • Batch Queueing   • Small local LLMs
   • Automated DB        for stream-ID    • Orphan context pad   • Inline Sub Editor  (Qwen / Llama)
     Backups           • Pre-flight VRAM  • TVDB clean/activate  • Live Progress Bar • Multi-target
```

### 1. ⚡ Quick Wins & Codebase Hygiene (Low Effort, Immediate Value)

#### 1.1 Fix Pre-Existing Test Failure (`test_glossary_profile.py`) -- **Done (2026-09-22)**
* **Issue:** `pytest tests -q` reports 1 failure out of 738:
  `RealGlossaryDataTests::test_loads_real_series_profile` fails because the test expects `"Eda Yıldız"` in canonical entities, whereas the live production glossary file in `/glossary` defines `"Eda"`.
* **Action:** Updated the test assertion in `tests/test_glossary_profile.py` to check for the real production canonicals (`"Eda"`, `"Serkan"`) and confirm `"Eda Yıldız"` now shows up as a surface-form alias instead, matching the current `love-is-in-the-air.yaml`. Suite is 100% green: **708 passed, 0 failed**.

#### 1.2 Automated SQLite Database Backups -- **Done (2026-09-22)**
* **Issue:** `scripts/backup_jobs_db.sh` exists and uses `sqlite3 ".backup"` (safe with WAL mode), but requires manual invocation.
* **Action:** Installed a daily host cron entry (`crontab -l`):
  ```
  0 3 * * * /opt/projects/subtitle-ai/scripts/backup_jobs_db.sh >> /opt/docker/appdata/subtitle-ai-v2/backups/backup.log 2>&1
  ```
  Logs to `backups/backup.log` alongside the `.db.gz` files themselves rather than `/var/log` (not writable by the deploy user). Verified with a manual run: wrote `jobs-20260922-192444.db.gz` successfully; a prior manual backup from 2026-09-21 was already present, confirming the script's real-world path is correct. 14-day retention (script default) applies automatically.

---

### 2. 🎮 GPU & Memory Optimization (Tesla P4 + Tdarr Coexistence)

#### 2.1 Lightweight Whisper Model for Stream Sampling (`audio_streams.py`) -- **Done (2026-09-23)**
* **Issue:** When an operator clicks "Analyze" in the web UI or when multi-window language identification runs, `asr.py` loads `large-v3` because it is the only model cached in `/models`. This spikes VRAM by ~3 GB for a short 20-second sample and creates severe VRAM contention with active ASR/translation jobs and Tdarr transcoding.
* **Action:** Added `SUBTITLE_AI_SAMPLE_MODEL` env var (default `"small"`, ~200 MB VRAM). `audio_streams.default_sampler()` now reads this env var via `_sample_model_name()` and attempts to load the configured lightweight model. If the model fails to load (e.g. not pre-cached in the read-only `/models` mount), it logs a warning and transparently falls back to `large-v3`, preserving existing behavior on day zero. Added `scripts/download_sample_model.py` — a one-time host-side helper to pre-cache the model into `${CONFIG_PATH}/subtitle-ai/models` before container restart. Updated `.env.example` with full documentation. 6 new unit tests cover env var reading, explicit model override, fallback-to-large-v3, and the `large-v3`-direct path.

#### 2.2 Dynamic VRAM Pre-Flight Headroom Checks -- **Done (2026-09-23)**
* **Issue:** On an 8 GB Tesla P4 sharing resources with Tdarr transcode workers, transcode bursts can suddenly reduce available VRAM below model allocation requirements, causing unrecoverable CUDA OOMs.
* **Action:** Added `gpu.preflight_vram_check(required_gb=DEFAULT_VRAM_MARGIN_GB, ...)`, polling `torch.cuda.mem_get_info()` and sleeping/retrying for a bounded window (default 20s, 2s poll interval) before raising `gpu.InsufficientVramError`. No-ops when CUDA isn't available (CPU CI). `DEFAULT_VRAM_MARGIN_GB` defaults to 3.2GB, overridable via `SUBTITLE_AI_VRAM_MARGIN_GB`. Wired into all three real in-process CUDA model constructions: `asr.py::transcribe()` (large-v3 ASR), `translate.py::load_model()` (NLLB, shared by both the local worker path *and* `translate_server.py`'s remote server — so this also covers the RTX 3070 host's Jellyfin/Plex transcode contention, not just the Tesla P4/Tdarr case), and `audio_streams.py`'s stream sampler (using a smaller 0.5GB margin for the lightweight model, the full default margin only for its `large-v3` fallback — and skipping that fallback attempt entirely if the lightweight check itself fails, since large-v3 needs strictly *more* VRAM, not less). 10 new unit tests (`tests/test_vram_preflight.py` + wiring tests in `tests/test_audio_streams.py`) cover the no-op/wait/timeout/margin-override paths and the fallback-skip behavior. Suite green: 737 passing, 0 failures.

---

### 3. 🎯 Pipeline & Subtitle Quality Enhancements

#### 3.1 Revive and Merge Phase 2 of "Unpunctuated Run-on Cues" -- **Done (2026-09-23)**
* **Issue:** Spoken dialogue that lacks sentence-ending punctuation cannot be split by `split_into_sentences()`. Sent to NLLB in a single large chunk, it can cause garbled translations or hallucinations.
* **Action:** Restored and merged Phase 2 from `git stash` into `subtitle_ai/translate.py` and `subtitle_ai/glossary.py`. Flagged unpunctuated run-on spans (8+ words without sentence-ending punctuation) now receive a bounded chunk-and-compare retry. If chunking preserves more protected entities (or prevents severe truncation), the chunked candidate is safely preferred. Tested with 13 unit tests in `tests/test_translate.py` and `tests/test_glossary.py`. Suite is 100% green (721 tests passing).

#### 3.2 Context Padding for Orphan Words in Songs/Dramatic Pauses
* **Issue:** When Whisper produces a large internal timestamp gap between words (frequent in sung lyrics or dramatic pauses), single words (e.g. Turkish *"Her"*) are isolated into standalone context spans. NLLB translates them in complete isolation, producing context-free nonsense (e.g., *"out"*).
* **Action:** When `translate.build_context_spans()` detects an isolated single-word cue adjacent to an acoustic gap, supply the preceding cue's text as soft context to ground the translation without altering target line segmentation.

#### 3.3 TheTVDB Client Integration Review
* **Issue:** `tvdb_client.characters()` is currently inactive (uncalled), and entity data relies entirely on YAML files and `auto_glossary.py`.
* **Action:** Either wire TVDB character lists into `auto_glossary.py` as candidate suggestions with confidence weighting, or cleanly deprecate and document the unused functions to eliminate dead code.

---

### 4. 🖥️ Web UI & Operator Experience

#### 4.1 Batch Job Enqueueing (Full Season / Directory Selection)
* **Issue:** In `LibraryPage.tsx`, operators must select and enqueue each episode one by one.
* **Action:** Add a multi-select checkbox or a "Queue Entire Season" button to enqueue all untranscribed episodes with standard parameters in a single click.

#### 4.2 Interactive Subtitle Review & Fix Editor
* **Issue:** The QC engine flags high-confidence issues under `needs_review` on `JobDetailPage.tsx`, but correcting them requires downloading the SRT, editing in an external editor, and re-uploading via Workflow B.
* **Action:** Implement an inline subtitle editor tab or modal on `JobDetailPage.tsx` filtering directly to `needs_review` cues, allowing the operator to correct a mistranslated word or line break and save the output atomically without re-running the GPU pipeline.

#### 4.3 Granular Real-Time Progress Reporting
* **Issue:** While the backend emits SSE events (`ASR_PROGRESS` with `position / total` and `TRANSLATION_PROGRESS` with `done / total`), the UI displays a generic "RUNNING" badge.
* **Action:** Render percentage-based progress bars and estimated completion times (ETA) on the Job cards and Job Detail view.

---

### 5. 🚀 Architectural Horizons: Local LLM Translation

#### 5.1 Evaluate Quantized Small LLMs (e.g., Qwen 2.5 7B / Llama 3.1 8B via vLLM)
* **Rationale:** While NLLB-200 (1.3B) is fast, conversational LLMs offer substantial advantages for subtitle translation:
  1. **Conversational Fluency:** Dramatically better handling of idioms, humor, and dialogue subtleties.
  2. **Native Entity Adherence:** Natural prompt-based following of character names and glossary terms without requiring fragile string placeholder substitution (`Xaa`, `Xab`).
  3. **Multi-Target Translation:** Cleanly unlocks reliable translation into languages other than English (e.g., Spanish, German, French) if desired in the future.
* **Deployment Path:** Can be hosted on the remote GPU server (RTX 3070 8GB) using 4-bit AWQ/GGUF quantization via Ollama or vLLM, interfaced via `translate_server.py`.
