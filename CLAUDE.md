# CLAUDE.md

Guidance for a future Claude Code session working in this repo. For
project architecture/workflows, see `README.md` first -- this file is
deploy/ops/precedent knowledge that isn't written down anywhere else.

## Tests

```bash
cd /opt/projects/subtitle-ai && PYTHONPATH=subtitle_ai uv run --with pytest pytest tests -q
```

Plain `python -m pytest` / `pytest` also work if your environment
already has the right interpreter on `PATH` and deps installed (see
`.github/workflows/test.yml` for the exact CPU-only CI setup) -- the
`uv run` form above is the one that reliably works from a fresh shell.

Baseline as of 2026-09-23: 757 passing, 0 failures. (History: 708 after
fixing `test_glossary_profile.py`'s stale `"Eda Yıldız"` canonical
assertion 2026-09-22 -- see `IMPROVEMENT_PLAN.md` section 1.1 -- then 727
after the lightweight-sampler-model tests (section 2.1), 737 after the
VRAM pre-flight tests (section 2.2), then 757 after the orphan-context-
padding tests (section 3.2). Update this line rather than leaving it to
drift the next time the count moves.)

Frontend: `cd frontend && npx tsc --noEmit && npm test -- --run`.

## Deploying

```bash
./scripts/deploy.sh [remote-host]     # default remote-host: media-server
```

Builds `subtitle-ai:dev`, redeploys the local `subtitle-ai` (master)
container, ships the same image to the remote GPU host, redeploys
`translate-server` there from `compose.translate-server.yml`, and polls
both health endpoints. Set `SKIP_REMOTE=1` to skip the remote leg (e.g.
a change that only touches frontend/API code, not `translate.py`/
`translate_server.py`).

Master health: `curl http://localhost:8099/api/health` -- reports
`worker_last_heartbeat_seconds_ago` when the worker thread is alive; a
large/growing value means the thread is wedged, not just busy (the
heartbeat updates on every pipeline-stage event, not just once per poll,
so a long-running job doesn't itself look like a stall).

Remote translate-server health: `curl http://<remote-host>:8091/health`.

## Scratch dirs and the remote translate-server (ops behavior)

- **`WORK_ROOT/<job_id>` is auto-managed** (`workdir.py`): removed when a
  job completes or is cancelled, kept `SUBTITLE_AI_FAILED_WORK_RETENTION_HOURS`
  (default 24) after a failure -- so a failed job's WAV/partial SRTs are
  still there to inspect for a day -- and removed on `DELETE /api/jobs/<id>`.
  A startup + hourly sweep also removes orphan dirs (no job row) by mtime.
  The FIRST sweep after deploying this cleared ~6GB of accumulated dirs;
  if you need a failed job's artifacts, copy them out before the window ends.
- **Output is English-only**: `target_lang` other than `en` is a 422
  (`output.TARGET_LANG`). Don't reintroduce free-form targets without
  changing `translate.load_model()`'s pinned `eng_Latn` BOS token first.
- **translate-server keeps ONE model** regardless of source language
  (~2.8GB; extra languages are tokenizers only), runs one request at a
  time, and never evicts while `active_requests > 0`. If remote VRAM
  climbs past ~3GB after multi-language use, that invariant is broken.
- **VRAM pre-flight check before every in-process CUDA model load**
  (`gpu.preflight_vram_check()`, wired into `asr.py::transcribe()`,
  `translate.py::load_model()` -- shared by the local worker path AND
  `translate_server.py`'s remote server -- and `audio_streams.py`'s
  stream sampler). Polls `torch.cuda.mem_get_info()`, waiting up to 20s
  (2s poll interval) for `SUBTITLE_AI_VRAM_MARGIN_GB` (default 3.2) free
  before raising `gpu.InsufficientVramError` instead of attempting a load
  that would very likely CUDA-OOM. Exists specifically for a Tdarr
  transcode burst on the Tesla P4 host or a Jellyfin/Plex transcode burst
  on the translate-server's RTX 3070 host landing in the gap between this
  project's own idle-eviction freeing memory and the next request needing
  it -- `gpu.gpu_lock()` alone only serializes this project's OWN
  processes against each other and can't see or wait out that kind of
  external contention. A raised `InsufficientVramError` surfaces as a
  normal job failure (nothing silently retries past it at the pipeline
  level) -- if you see these in logs, it means the card was genuinely
  starved by something outside this project's control, not a bug here.
- **NLLB decode passes `clean_up_tokenization_spaces=True` explicitly**
  (`translate.py::_generate_one_batch`). This tokenizer's own default
  (transformers 4.48) is False unless overridden, which leaked raw
  subword spacing into real output ("That 's nice .", "go ."),
  independent of the Title Case source-casing issue below -- confirmed
  reproducing on ordinary sentence-case input too. If you ever see that
  pattern in output again, check this first before assuming it's back.
- **ASR hotwords are OFF and VAD is permissive** (`asr.py` docstring has
  the measurements). The hotword list induced Title Case transcripts
  (~70% of segments) that NLLB turns into token-spaced English, and it
  dropped audio windows. Tempting to re-enable to fix name spelling --
  check recall against a human SRT first. Existing `.tr.srt` files for
  Love Is In The Air E01-E05 predate this and are still Title Case.
  `auto_glossary` mining of those Title Case files also polluted the
  suggestion list with ordinary words ("Anladım", "Buyurun").
- **Orphan single-word cue near a real acoustic gap: soft-context
  grounding, not a segmentation change** (2026-09-23, IMPROVEMENT_PLAN.md
  3.2). Real example, S01E01's closing song: a large internal word-
  timestamp gap inside one Whisper segment split "Her" from "şey olur,
  her şey biter", and `translate.build_context_spans()`'s
  REAL_ACOUSTIC_GAP handling correctly-by-design refuses to MERGE across
  that pause -- but translating "Her" with zero context produced garbage
  ("out"). Deliberately NOT fixed by loosening REAL_ACOUSTIC_GAP handling
  (see the historical reasoning below, still accurate): `transcript.py`'s
  BoundaryReason/MERGEABLE_BOUNDARIES docstrings and `projection.py` both
  depend on that boundary being trustworthy for coverage validation.
  Instead, `translate._pad_orphan_context()` (a post-pass in
  `translate_spans()`, toggle: `SUBTITLE_AI_ORPHAN_CONTEXT_PADDING`,
  default on) detects a single-cue, single-word span adjacent to EXACTLY
  ONE real-gap boundary, translates a short probe (the gap-side
  neighbor's nearest cue, alone and padded with the orphan word), and
  replaces the orphan's translation with the extracted residual ONLY when
  `translate._strip_anchor_affix()` finds an exact word-for-word match
  between the probe's anchor portion and that neighbor's own independent
  translation -- segmentation/display boundaries are never touched, and
  any non-exact match silently keeps today's (context-free) translation.
  Confirmed narrow in practice (not present in 20 minutes of ordinary
  S01E01 dialogue checked by hand) -- concentrated in sung-lyric sections
  where Whisper's word-level alignment is least reliable -- so this pass
  costs nothing on an ordinary job (zero orphans found -> zero extra
  model calls) and the exact-match requirement means it only ever
  improves a translation, never silently guesses one.
- `SKIP_REMOTE=1 ./scripts/deploy.sh` is right for changes that don't
  touch `translate.py`/`translate_server.py` (API, worker, scratch
  cleanup); anything in those two files needs the full deploy.

## Environment / secrets

Copy `.env.example` to `.env` and fill in `DATA_PATH`/`CONFIG_PATH`.
Everything else in it is optional with a working default. Don't confuse
this with a homelab-wide `.env` some deployments symlink into this
project directory for unrelated services -- if you see one, it's not
this project's config.

## Data durability

- **Glossary YAML** (`${CONFIG_PATH}/subtitle-ai/glossary/*.yaml`,
  mounted read-write at `/glossary`) is its OWN git repo, initialized in
  place directly on the host -- NOT part of this repository. If you edit
  a glossary file as part of a fix, `cd` into that directory on the host
  and commit there too; this repo's git history won't show it.
- **Job database** (`${CONFIG_PATH}/subtitle-ai-v2/cache/jobs.db`,
  SQLite/WAL): back up with `scripts/backup_jobs_db.sh` (safe to run
  against the live DB). No cron job is installed automatically -- see
  that script's header for the recommended line if you want scheduled
  backups.

## QC is advisory, not a gate -- on purpose

`pipeline.py`/`srt_translation.py`'s `valid` computation only fails a
job on segmentation/timing/output-structural QC findings.
Translation/entity/hallucination/readability findings never block
completion -- real, measured false-positive rates (harmless
interjection-collision "substitution" matches, embellishment-but-not-
wrong translation_error cases) were too high to safely auto-fail on.
Instead, `JobQc.needs_review_count()` (`qc/types.py`) surfaces only
high-confidence findings (entity_error/hallucination categories, or
anything >=0.7 confidence) as a `needs_review` count on the job record,
shown ahead of the generic QC summary in the job list GUI. If you're
tempted to make QC block completion, check the false-positive rate on
real data first -- it's higher than it looks from reading the heuristics
alone.

## Entity-protection precedent: the evidence bar

Before adding a name to a series glossary's `protected: true` list
(`glossary/<series>.yaml`), the established bar from real investigations
this project has done is: (1) confirm it's a genuinely recurring
character via corpus-wide occurrence counts across many episodes, not
just the one flagged instance, AND (2) find at least one concrete
example of it being mistranslated/hallucinated -- a common-word
collision ("Cenk" = "war", "Evren" = "universe", "Erdem" = "virtue",
"Deniz" = "sea"), a bare-exclaimed-name hallucination ("Sirius!" ->
"Sirius, what are you doing?"), or inconsistent transliteration. A name
that's merely recurring but shows no confirmed bug (e.g. "Ayfer" in the
Love Is In The Air investigation) is deliberately left unprotected --
protection isn't free of risk for common-word collisions, so don't add
it speculatively. A one-scene character (e.g. "Fatma", "Faruk") is
excluded regardless of translation quality, on recurrence alone.

`auto_glossary.py` mines a series' own already-completed episodes for
candidate names automatically, but only feeds ASR hotwords, never
translation protection directly -- promoting a mined name to `protected:
true` is always a deliberate human/session decision, never automatic.

## TheTVDB integration is metadata-only, unused for entities

`tvdb_client.py` exists and works (series title enrichment is wired into
`glossary_profile.load_profile()`), but `tvdb_client.characters()` --
fetching TheTVDB's cast list -- is dead code, called nowhere. No
`TVDB_API_KEY` is configured in production, so none of this currently
does anything. Don't assume TVDB is a source of entity/character data
for the glossary; `auto_glossary.py`'s corpus-mining is the only thing
that actually populates candidate names today, and it works from the
show's own real dialogue, not billing/cast metadata.

## Readability vs. content-completeness: an accepted, disclosed tradeoff

Fixing multi-sentence content-truncation (a source cue with 2+ real
sentences silently losing everything after the first) increased
readability-QC findings season-wide, because the fix means more cues,
which means less time-per-cue on average. This was judged worth it
(content correctness over cosmetic pacing) and is NOT something to
"fix" by reverting the truncation fix. A follow-up (two-speaker dash
dialogue kept as one cue for its whole envelope,
`segmentation_target.segment()`) recovered some of the readability
regression without sacrificing content. The remaining gap above the
pre-fix baseline is accepted, not a bug.

## Unpunctuated run-on source cues: shipped

A source cue with zero sentence-ending punctuation (can't be split by
`glossary.split_into_sentences()`) sometimes gets garbled by NLLB in one
`generate()` call -- real example, a source cue containing an aside
("oh, dear Selin") translated as "I'm Moon Flood." **Phase 1** (QC
visibility only -- flags this shape as a `translation_error` finding,
changes no translation behavior) shipped first. **Phase 2** (a bounded
chunk-and-compare retry, validated on real data: 227/1157 flagged cues
with a protected-entity signal genuinely improved, zero regressions in a
manual sample) was merged: flagged run-on sentences are translated once
normally, then retried in fixed word chunks (`_chunk`), preferring the
chunked candidate whenever entity preservation or length-ratio checks
indicate superior content preservation. Covered by unit tests in
`tests/test_translate.py` and `tests/test_glossary.py`.
