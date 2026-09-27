# Subtitle AI: Enhancement Draft (Round 2)

*Draft, 2026-09-28. Successor to `IMPROVEMENT_PLAN.md`, whose sections 1-4
are all done and whose 5.1 was evaluated and deferred.*

Every item below comes from a survey of the code **and** of the live
deployment on myphy-ai: the job database (300 jobs: 290 SRT translation,
10 video), the appdata directories, the running container and the GPU.
Where a number is quoted, it was measured, not estimated. Where something
is a hypothesis still to be checked, it says so.

## 0. Why a second round

The first plan was written for a **shared Tesla P4** (Tdarr transcodes
competing for the same 8GB), and many of its defaults are deliberate
defensive trade-offs against that contention. Since the 2026-09-27
migration, subtitle-ai has a **dedicated RTX 3070**. Nothing else uses the
card (237MiB used at idle), and no remote translate-server exists. Several
of those trade-offs now cost throughput and protect against nothing. The
migration also quietly dropped one piece of operational setup.

Items are grouped by priority, not by area:

| # | Item | Priority | Effort | Evidence |
|---|------|----------|--------|----------|
| A1 | DB backups stopped running after the migration | **P0** | XS | no crontab, empty `backups/` |
| A2 | Glossary data repo has an uncommitted edit, and promote never commits | P1 | S | `git status` in glossary dir |
| A3 | `deploy.sh` reports success even when the app never became healthy | P1 | XS | script reading |
| A4 | Non-reproducible Python builds (`uv.lock` untracked, `uv sync` not frozen) | P1 | XS | `git status`, Dockerfile |
| B1 | Re-tune NLLB for a dedicated card (batch/beams/`empty_cache`/length sort) | **P1** | S | 5-7 sent/s on big episodes vs 16.5 benchmark |
| B2 | Keep NLLB resident across consecutive jobs | P1 | M | model reloaded for every job, 11s first-batch latency |
| B3 | CTranslate2 backend for NLLB (benchmark first) | P2 | M | `ctranslate2` already a dependency |
| B4 | Re-benchmark video ASR on float16 and recalibrate progress/ETA | P2 | S | ETA milestones calibrated on the P4 |
| C1 | `needs_review` is 99.97% one noisy rule | **P0** | XS | 62,490 of 62,512 hits are the min-duration rule |
| C2 | QC/log payload bloat in the job row | P3 | S | ~100KB QC + ~30KB log per job |
| D1 | Enabling `SUBTITLE_AI_API_KEY` breaks the web UI | P2 | S | frontend never sends `X-API-Key` |
| E1 | Revisit 5.1 (local LLM) under the new host's constraints | P3 | L (scoping only) | VRAM assumption changed |

Suggested order: **C1 → A1 → A3/A4 → B1 → A2 → B2 → B4 → D1 → B3 → C2 → E1.**
C1 and A1 are both about an hour each and fix things that are wrong today.

---

## A. Operational regressions and hygiene

### A1. Database backups are not running on the new host -- P0

* **Evidence:** `IMPROVEMENT_PLAN.md` 1.2 says a daily 03:00 cron runs
  `scripts/backup_jobs_db.sh`. On myphy-ai, `crontab -l` returns
  *"no crontab for badrul"*, and `/opt/docker/appdata/subtitle-ai/backups/`
  is empty. The crontab was a per-host artifact that the migration didn't
  carry over. `CLAUDE.md` also still says *"No cron job is installed
  automatically"*, which contradicts 1.2.
* **Proposal:**
  1. Re-install the cron line from 1.2 on myphy-ai and run it once by hand
     to confirm it writes `jobs-*.db.gz`.
  2. Make it survive the next migration: add a short "Host setup
     checklist" section to `CLAUDE.md` (cron line, `uv`/`ffmpeg`/`node`,
     `gh auth setup-git`, `.env` fields). The migration already rediscovered
     three of these by hand.
  3. Optional: have `/api/health` report `last_backup_age_hours` (newest
     file mtime in the backups dir, if that dir is mounted) so that a
     silent stop shows up somewhere.
* **Acceptance:** a `.db.gz` file less than 24h old exists on any given day,
  and the checklist exists in `CLAUDE.md`.

### A2. Glossary data repo: uncommitted edits and no auto-commit

* **Evidence:** `/opt/docker/appdata/subtitle-ai/glossary` is its own git
  repo (`CLAUDE.md`: *"commit there too"*). It has exactly one commit
  (`Initial commit`, 2026-09-21), and `love-is-in-the-air.yaml` has been
  modified and uncommitted since 2026-09-22. The UI's
  promote/update/delete endpoints (`api.py:652-770`) write those files
  directly, so every in-app glossary edit is unversioned by construction.
  The "its own git repo" safety net only works if a human remembers.
* **Proposal:** after a successful glossary write, run
  `git add <file> && git commit -m "<action> <entity> via UI"` in the
  glossary dir. This is best-effort: log and continue if git is missing or
  the dir isn't a repo, and never fail the request. Commit the pending
  2026-09-22 edit by hand first, after reviewing the diff.
* **Needs a decision:** the container would need `git` (a small
  `apt-get install git`) plus a committer identity via env. The
  alternative is a host-side cron that auto-commits the dir, which is
  zero code but coarser history.
* **Acceptance:** promoting an entity in the UI produces a commit in the
  glossary repo, and the API tests cover the "not a repo" no-op path.

### A3. `deploy.sh` never fails on an unhealthy deploy

* **Evidence:** both health loops (`scripts/deploy.sh`) `break` on success
  but just fall out of the `for` after 30 tries, so the script prints
  `==> Done` and exits 0 when the container never became healthy. Its
  header comment also still says it deploys *"to both the local (master)
  and remote hosts"*, which stopped being the default on 2026-09-27.
* **Proposal:** track success in a flag and `exit 1` with the last
  `docker logs --tail 50 subtitle-ai` output if the loop times out. Fix
  the header comment.
* **Acceptance:** `docker compose stop subtitle-ai` followed by a deploy
  whose container can't start exits non-zero.

### A4. Reproducible Python builds

* **Evidence:** `uv.lock` (112KB) is untracked. The Dockerfile runs
  `uv sync --no-install-project` without the lock, so it resolves fresh on
  every uncached build. CI installs a *different*, unpinned set via `pip`
  (`pytest`, and `torch` from the CPU index, with no version pin).
  Top-level pins in `pyproject.toml` protect direct dependencies, not
  transitive ones.
* **Proposal:** commit `uv.lock`, `COPY` it into the image and use
  `uv sync --frozen --no-install-project`. Optionally, move CI onto
  `uv sync --frozen` too, with the torch CPU index as a CI-only override.
  That's a larger change, and the gain is only drift between CI and prod.
* **Acceptance:** two builds a week apart produce the same
  `uv pip freeze`.

---

## B. Throughput on a dedicated GPU

### B1. Re-tune NLLB translation for a dedicated card -- P1

* **Evidence:** measured from job logs, translation stage only
  (`TRANSLATION_STARTED` → last `*_TRANSLATION_PROGRESS`), all on an
  RTX 3070:

  | Job (created) | Sentences | Stage time | Sent/s |
  |---|---|---|---|
  | 2026-09-27 20:20 | 2,502 | 350s | **7.2** |
  | 2026-09-26 08:13 | 513 | 44s | 11.6 |
  | 2026-09-26 08:12 | 462 | 29s | 15.7 |
  | 2026-09-25 23:16 | 2,450 | 391s | **6.3** |
  | 2026-09-25 18:31 | 2,273 | 439s | **5.2** |

  Small files run close to the 16.53 sent/s benchmark in `translate.py`.
  Full episodes run at a third to half of it. SRT jobs have a median of
  168s and a p90 of 494s, so translation is almost all of that. The
  current settings are *explicitly* P4/Tdarr defences
  (`TranslationConfig`, `translate.py:200-224`):
  - `batch_size` 12 → **8** and `num_beams` 4 → **2**, both lowered
    2026-09-19 *"so Tdarr's worst case doesn't exhaust the card"*;
  - `free_gpu()` / `empty_cache()` after **every** chunk
    (`translate_batch()`), which forces a CUDA sync and discards the
    allocator cache ~300 times per episode;
  - sentences are batched in document order, with no length sorting, so
    each batch pads to its longest sentence.
* **Hypothesis to check first:** the drop on long episodes has more than
  one cause. Candidates are the per-chunk `empty_cache()` churn, padding
  waste, and the run-on chunk retry (`_apply_chunk_retry`), which runs
  after the progress counter finishes. **Profile before changing
  anything**, using one full-episode SRT and a timer around each phase.
* **Proposal:**
  1. Add a benchmark script (`scripts/bench_translate.py`) that runs a
     fixed real episode's sentences through `translate_batch()` with
     configurable batch size, beams and sorting, and reports sent/s and
     peak VRAM. Keep the output next to `benchmark-results/`.
  2. Make batch size and beams env-overridable (`SUBTITLE_AI_NLLB_BATCH`,
     `SUBTITLE_AI_NLLB_BEAMS`), keeping today's values as defaults, the
     same pattern as `SUBTITLE_AI_COMPUTE_TYPE`.
  3. Sort by token length inside `translate_batch()` and scatter the
     results back to their original order. This is output-identical:
     every sentence is translated independently, and the tests can
     assert byte-identical output.
  4. Only call `empty_cache()` per chunk when a flag says the GPU is
     shared. The OOM-halving retry stays as the safety net.
* **Quality guard:** raising `num_beams` changes output. If beams go back
  to 4, diff a full episode before and after and spot-check it. Batch
  size and sorting should not change output, and a test should prove
  that.
* **Acceptance:** at least 2x sent/s on a 2,000+ sentence episode, with
  byte-identical output for the non-beam changes.

### B2. Keep NLLB resident across consecutive jobs

* **Evidence:** `translate_spans()` loads NLLB with `load_model()` and
  frees it in `finally` for every job (`translate.py:700-714`). The
  2026-09-27 job's first progress event arrived 11.0s after
  `TRANSLATION_STARTED`. Batch queueing (plan 4.1) is now how seasons get
  processed: 183 jobs were created on 2026-09-20 alone, and each one paid
  the load. Per-job loading was right on a shared card, where holding
  2.6GB between jobs starved Tdarr. It is no longer necessary.
* **Proposal:** keep a process-level model holder with an idle-eviction
  timer (e.g. `SUBTITLE_AI_MODEL_IDLE_SECONDS`, default 300; `0` gives
  today's behaviour). Use it only while the worker's queue has more work
  of the same kind. The holder must still cooperate with `gpu_lock()` and
  with the ASR load: large-v3 fp16 (~3GB) plus NLLB fp16 (~2.6GB) fit in
  8GB, but that needs verifying with `nvidia-smi` during a real video job
  before relying on it. `gpu.py`'s module docstring already expects a
  "future concurrent-worker design can share" models, so this is the seam
  it anticipated.
* **Acceptance:** the second of two back-to-back SRT jobs shows a
  first-batch latency under 1s, and idle VRAM returns to baseline after
  the timeout.

### B3. CTranslate2 backend for NLLB (benchmark-gated)

* **Rationale:** `ctranslate2==4.4.0` is already in the image for
  faster-whisper. NLLB converts with `ct2-transformers-converter`, and
  CTranslate2 usually runs seq2seq models several times faster, in less
  memory, than HF `generate()`. That's a general expectation, not a
  measurement on this card.
* **Why P2 and not P1:** the output differs from HF generation. Several
  hard-won fixes depend on exact generation behaviour
  (`no_repeat_ngram_size=4`, `clean_up_tokenization_spaces=True`, the BOS
  pin, placeholder repair). Each one needs re-validating against real
  output. Do B1 first. If B1 closes most of the gap, B3 may not be worth
  its risk.
* **Proposal:** if B1 leaves translation as the bottleneck, convert the
  model into `/models`, add `SUBTITLE_AI_NLLB_BACKEND=hf|ct2` (default
  `hf`), and run B1's benchmark plus a full-episode output diff before
  changing the default.

### B4. Re-benchmark video ASR on float16 and recalibrate progress/ETA

* **Evidence:** video jobs have a median of 34 minutes and a p90 of 57
  minutes (10 jobs), almost all on the P4 with `int8`. The host now runs
  `SUBTITLE_AI_COMPUTE_TYPE=float16` on Ampere, but no post-migration
  video benchmark is recorded. `worker.py`'s milestone table assumes ASR
  is ~70-75% of a video job, *"matching this project's own measured
  real-episode timing"*, measured on the old card. If ASR got several
  times faster, the progress bar and ETA are now skewed.
* **Proposal:** run one full episode, compare it with
  `benchmark-results/season-01-full-39-episode-results.json`, record the
  result, and update the milestone percentages if the ASR share moved by
  more than ~10 points.

---

## C. Quality-control signal

### C1. `needs_review` is almost entirely one noisy rule -- P0

* **Evidence (all 300 jobs):** of **62,512** findings that count toward
  `needs_review`, **62,490 (99.97%)** are one rule: readability's
  *"duration < 1.0s"*, emitted at confidence **0.7**
  (`qc/readability_qc.py`), exactly the `REVIEW_CONFIDENCE` threshold in
  `qc/types.py`. The categories `needs_review` was designed to surface
  (`entity_error`, `hallucination`) total **21** across all 300 jobs.
  Average `needs_review` is 66 per SRT job and 96 per video job, so the
  badge is always large and carries no information.
* **Worse for Workflow B:** SRT translation keeps the source file's cue
  timing, so a 0.95s cue came from the uploader's subtitle. The operator
  can't fix it in the SRT editor, which is text-only by design.
* **Proposal (smallest change first):**
  1. Emit the min-duration finding at confidence **0.5**, like the CPS
     rule (which already fires 125,818 times at 0.5 and correctly stays
     out of `needs_review`). The finding stays visible in the QC list.
  2. Optionally, keep readability out of `needs_review` entirely, and
     keep `needs_review` for entity/hallucination plus high-confidence
     *translation* findings, which is what the `CLAUDE.md` "QC is
     advisory" section describes it as.
  3. Recompute `needs_review` for existing rows with a one-off script,
     or leave old rows alone and say so.
* **Acceptance:** re-running `needs_review_count()` over the 300 stored QC
  blobs drops the total from 62,512 to about 21, and a test pins
  "min-duration findings don't count toward review".

### C2. QC and log payloads in the job row

* **Evidence:** the average job row carries **~99KB** of QC JSON and
  **~30KB** of log. The latest job had 628 readability findings,
  mostly the low-confidence CPS rule. Its log had 326 entries, of which
  ~310 were `SRT_TRANSLATION_PROGRESS` ticks (one per batch). `jobs.db`
  is 38MB at 300 jobs, and the list/SSE endpoints serialize more than
  they need to.
* **Proposal:** (a) keep per-cue detail only for findings at 0.6
  confidence or above, and store the rest as `{rule: count}` summaries;
  (b) throttle progress log lines to one per ~5% (the `stage`/`progress`
  columns already carry live progress, per plan 4.3). The frontend's
  `QcFindingsList` needs a small change to render the counts.
* **Priority note:** P3. It's cheap now and gets expensive later, but it
  isn't hurting anything yet.

---

## D. Access control

### D1. Setting `SUBTITLE_AI_API_KEY` would break the UI

* **Evidence:** `require_api_key()` guards cancel, retry, delete,
  glossary writes and `PUT /api/jobs/{id}/srt`. `grep -ri 'x-api-key'
  frontend/src` finds nothing, so the SPA never sends the header. The key
  is unset today, so nothing is broken yet. But the documented way to
  harden the deployment (`.env.example`, `compose.yml`) silently disables
  the UI's own buttons with a 401.
* **Proposal:** either (a) have the UI prompt for the key once on the
  first 401, store it in `localStorage`, and send it from
  `api/client.ts`, or (b) document that the key is for non-UI callers
  only, and make the 401 message say so. (a) is small. The job-creation
  endpoints are deliberately unguarded (see `require_api_key`'s
  docstring), so this is only about the mutating actions.

---

## E. Architectural horizon

### E1. Revisit 5.1 (local LLM translation) with the new constraints

`IMPROVEMENT_PLAN.md` 5.1 deferred a local 7-8B LLM partly because *"the
target host … is the SAME card … shared with Jellyfin/Plex"*. That reason
is gone. The card is dedicated now, so a ~5-7GB Q4 model is a
**time-slicing** problem (load under `gpu_lock()` after ASR frees
large-v3) rather than a contention problem. The other reasons stand:
unmeasured throughput, new serving surface, and the DLX lesson about
building before a real smoke test.

**Recommended next step is still scoping, not code:** decide the engine's
role (selectable per job vs. replacement), then run a **blind A/B on ~50
real sentences** from the 290 translated episodes (NLLB vs. candidate
LLM), including known-hard cases: the entity collisions in the
`CLAUDE.md` evidence-bar section, the run-on cues, and the orphan
lyrics. Do this after B1/B2, so the comparison is against NLLB at its
real speed on this card, not its handicapped P4 settings.

---

## Explicitly not proposed

* **Loosening `REAL_ACOUSTIC_GAP` merging, re-enabling ASR hotwords,
  making QC gate completion.** Each has a documented, data-backed reason
  in `CLAUDE.md` to stay as is, and nothing in this survey contradicts
  them.
* **Reviving TVDB cast lookup.** There is still no API key configured
  (`TVDB_API_KEY` is empty on myphy-ai), so the 3.3 reasoning holds.
* **Changing the translate-server remote path.** It's unused but working
  and tested. Leave it until there's a second GPU host again.
