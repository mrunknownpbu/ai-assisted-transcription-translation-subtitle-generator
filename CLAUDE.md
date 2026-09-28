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

Baseline as of 2026-09-28: 1065 passing, 0 failures (plus 89 frontend
tests -- `cd frontend && npm test -- --run`; up from 939 after the
natural-dialogue plan's five steps -- see the segmentation-naturalness,
turn-detection and ASR-style entries elsewhere in this file). Every
backend test is `unittest`-style, so with no pytest available this also
works:
`cd subtitle_ai && PYTHONPATH=.:../tests ../.venv/bin/python -m unittest discover -s ../tests -t ../tests`.
CI installs an explicit package list (`.github/workflows/test.yml`), not
uv.lock -- a new runtime dependency must be added there too (ruamel.yaml
was, 2026-09-28; numpy was, 2026-09-28, for turns.py -- see pyproject.toml).
Verified directly on the
current host (myphy-ai), not just in CI or inside Docker: 0 skips once
`ffmpeg`, `uv`, and `node`/`npm` are on the bare host too, not just inside
the image -- CI's own setup installs `ffmpeg` as a separate step for the
same reason (see `.github/workflows/test.yml`). (History: 708 after
fixing `test_glossary_profile.py`'s stale `"Eda Yıldız"` canonical
assertion 2026-09-22 -- see `IMPROVEMENT_PLAN.md` section 1.1 -- then 727
after the lightweight-sampler-model tests (2.1), 737 after VRAM
pre-flight (2.2), 757 after orphan-context-padding (3.2), 756 after
removing `tvdb_client.characters()`'s dead-code test (3.3), 768 after the
worker stage/progress tests (4.3), 795 after the batch-queueing and
SRT-editor tests (4.1/4.2), then 830 (2026-09-25) before the run that took
it to 876: WebVTT-as-`.srt` source support, the Series page's folder-name
title fallback and episodes-vs-jobs count fix, `.vtt` uploads, the Jobs
page Refresh-button fix, and `SUBTITLE_AI_COMPUTE_TYPE`; then 939 after
ENHANCEMENT_DRAFT.md's round (2026-09-28). Update this line rather than
leaving it to drift the next time the count moves.)

Frontend: `cd frontend && npx tsc --noEmit && npm test -- --run`.

## Deploying

```bash
./scripts/deploy.sh                   # local only (the default since 2026-09-27)
./scripts/deploy.sh <remote-host>     # also redeploy a remote translate-server, if one exists
```

Builds `subtitle-ai:dev` and redeploys the local `subtitle-ai` container.
The remote leg (ship the same image to `<remote-host>`, redeploy
`translate-server` there from `compose.translate-server.yml`, poll its
health) only runs if you pass a host. It used to default to
`media-server` -- exactly backwards for a fresh checkout on a new host
(see "Host" below): it would have silently shipped an image to, and
redeployed, an old deployment's remote server that this checkout has
nothing to do with. `SKIP_REMOTE=1` still forces the remote leg off even
if you do pass a host.

App health: `curl http://localhost:8099/api/health` -- reports
`worker_last_heartbeat_seconds_ago` when the worker thread is alive; a
large/growing value means the thread is wedged, not just busy (the
heartbeat updates on every pipeline-stage event, not just once per poll,
so a long-running job doesn't itself look like a stall).

Remote translate-server health (only relevant if one is actually
deployed -- see "Host" below): `curl http://<remote-host>:8091/health`.

## Host

subtitle-ai runs on **myphy-ai** (`10.1.1.110`, LAN; `10.1.20.110`,
storage/NFS subnet; SSH alias `myphy-ai`) since a 2026-09-27 migration to
dedicated hardware -- a Ryzen 5 5500 + RTX 3070, nothing else sharing the
GPU. No remote translate-server is currently deployed anywhere
(media-server's was decommissioned the same day as part of the
migration); the code path still exists and works (`TRANSLATE_SERVER_URL`,
`translate_server.py`), it's just unset today. The prior host (a Tesla
P4 shared with Tdarr transcode workloads) was fully purged that day --
container, image, appdata (models/glossary/cache/backups), Docker build
cache, and the repo checkout itself -- so don't expect to find anything
subtitle-ai-related there again without redoing the migration.

`git`/`gh` are set up on myphy-ai so a session can commit/push and use
`gh` directly from there: origin is the HTTPS remote, and
`gh auth setup-git` supplies push credentials from the existing `gh`
login -- no separate SSH deploy key was needed. Running the test suite
directly on the bare host (not just inside Docker, where they're already
present) needs `uv`, `ffmpeg`, and `node`/`npm` -- all three were missing
on the fresh Ubuntu install and were installed as part of this migration.
If a host is ever set up from scratch again, don't forget them: the
Docker image having a dependency says nothing about the bare host having
it, and `uv run --with pytest`/`npm test` above need the bare host's own
copies.

### Host setup checklist

Everything here lives on the host, not in the image or the repo, so a
new host needs each one redone by hand. The 2026-09-27 migration had to
rediscover most of them one failure at a time:

1. `uv`, `ffmpeg`, `node`/`npm` on the bare host (tests; see above).
2. `gh auth login` + `gh auth setup-git` (push over HTTPS).
3. `.env` from `.env.example` (`DATA_PATH`, `CONFIG_PATH`, and
   `SUBTITLE_AI_COMPUTE_TYPE` to match the GPU; optionally the TMDB/TVDB,
   Sonarr/Radarr and Plex/Jellyfin keys).
4. Daily backup cron (`crontab -e`):
   `0 3 * * * /opt/projects/subtitle-ai/scripts/backup_jobs_db.sh >> /opt/docker/appdata/subtitle-ai/backups/backup.log 2>&1`
   -- then run the script once by hand and check a `.db.gz` appears.
   (It needs no `sqlite3` CLI: it falls back to python3's backup API.)
5. Copy `${CONFIG_PATH}/subtitle-ai/{models,glossary,cache}` across;
   the glossary directory is its own git repo -- copy `.git` too.

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
  that would very likely CUDA-OOM. Exists specifically for a burst from
  another process sharing the same physical card (this project's real
  experience: a Tdarr transcode burst on one host, a Jellyfin/Plex
  transcode burst on another) landing in the gap between this project's
  own idle-eviction freeing memory and the next request needing it --
  `gpu.gpu_lock()` alone only serializes this project's OWN
  processes against each other and can't see or wait out that kind of
  external contention. A raised `InsufficientVramError` surfaces as a
  normal job failure (nothing silently retries past it at the pipeline
  level) -- if you see these in logs, it means the card was genuinely
  starved by something outside this project's control, not a bug here.
- **GPU profile: dedicated by default** (`gpu.gpu_shared()`,
  `SUBTITLE_AI_GPU_SHARED`, 2026-09-28). The earlier defaults were P4 +
  Tdarr defences; on the dedicated 3070 NLLB runs batch 32 (not 8), skips
  the per-batch `empty_cache()`, and stays loaded between jobs
  (`translate._ResidentNllb`, `SUBTITLE_AI_MODEL_IDLE_SECONDS`, default
  600). Measured: an SRT job 354s -> 54s; a full video job ~7 min (ASR
  ~82%). Set `SUBTITLE_AI_GPU_SHARED=1` on any card shared with
  transcoders to get the old behaviour back.
- **A resident model outlives the job's `gpu_lock()`** -- so residency has
  its own cross-process protocol in gpu.py (`claim_residency()`,
  `evict_other_processes()`, `<lock>.resident` / `<lock>.evict-request`).
  Anything that loads a GPU model must go through
  `preflight_vram_check()`, which runs it. A script that loads a model
  some other way while the app has NLLB resident WILL OOM -- that exact
  failure (an eval script vs the app's idle 2.8GB) is why this exists.
- **NLLB runs on CTranslate2 by default** (`SUBTITLE_AI_NLLB_BACKEND`,
  2026-09-28), a float16 conversion at
  `${CONFIG_PATH}/subtitle-ai/models/ct2/` made by
  `scripts/convert_nllb_ct2.py`. Same measured quality as the hf path,
  2.9x faster. Without the conversion it falls back to hf with a warning.
  `_generate_one_batch()` picks the path from what `load_model()` actually
  returned (a str target token = CTranslate2, an int = hf), never from the
  config, so a fallback can't be misrouted. Any generation fix (like the
  `clean_up_tokenization_spaces` one below) must go into BOTH
  `_generate_one_batch` and `_generate_one_batch_ct2`.
- **Measure before changing translation behaviour**:
  `scripts/bench_translate.py` (speed, which lines change) and
  `scripts/eval_translation.py` (chrF vs the library's human
  `.en.hi.srt` subtitles through the real Workflow B path, with bootstrap
  CIs and blind A/B sheets), and `scripts/eval_transcription.py` (WER vs
  the human `<lang>.srt`, dialogue and lyrics scored separately, name
  recall; it skips references this app wrote itself). Results live in
  `benchmark-results/`. ASR baseline 2026-09-28 (7 LIITA S01 episodes):
  dialogue WER 19.8%, name recall 84.9%, lyrics coverage 10.7% -- the
  top name losses are near-miss confusions (Aydan -> "Aydın" x33,
  Selin -> "Selim", Melo -> "Melih").
- **`--segmentation` (2026-09-28)**: rebuilds `segmentation_source.build_cues`
  from each system's own kept words and compares the cues against the
  human cues of the same episode -- boundary precision/recall/F1 (+-0.4s),
  turn-change recall (human two-speaker "- A / - B" cues), % cues that
  overflow 2x42 chars or end mid-sentence, mean cue duration, and
  interjection recall. `cache` needs no GPU, but only reuses a transcript
  whose ASR settings match `asr.AsrConfig()` exactly (see `cached_transcript()`);
  most of the library's cached transcripts predate the `float16` compute-type
  default (int8, from before 2026-09-27's dedicated-GPU migration) and don't
  qualify, so the natural-dialogue-plan's intended 7-episode GPU-free
  baseline is currently thin: only S01E01 had a matching cache (the other
  6 need re-running ASR to get a like-for-like baseline). Measured 2026-09-28,
  S01E01 only (`benchmark-results/segmentation-naturalness-baseline-2026-09-28.json`):
  boundary F1 63.5% (P 60.8% / R 66.4%), turn recall 50.0% of 12 human
  two-speaker turn points, 23.1% of our cues overflow the display box vs
  0.0% of human cues, 12.8% of our cues end mid-sentence vs 0.4% human,
  mean cue duration 1.92s vs human 2.21s (2035 cues vs human 1863),
  interjection recall 78.7% of 89 -- i.e. interjections mostly survive
  already; the real gaps are cue count/duration (cues that don't map to a
  clean sentence or turn) and the total absence of turn-aware splitting
  (0% turn recall is impossible to improve without a turn detector, so
  the 50% here is boundaries that happen to land near a turn point by
  coincidence, not evidence of turn awareness).
- **`segmentation_source.build_cues` rewrite (2026-09-28, natural-dialogue
  plan step 2)**: two passes -- ACOUSTIC grouping only (a real gap or
  MAX_DURATION), then SENTENCE splitting within each group with no
  minimum length (the old `len(cur_text) >= 12` gate is gone -- a short
  "Tamam." right before a pause is now its own cue instead of glued onto
  the next sentence), a too-long sentence split at the best clause/
  conjunction boundary (`text_segmentation.py`, ported from
  `segmentation_target.py`'s already-validated algorithm, Turkish-lexicon
  tie-breaks) instead of an arbitrary 84-char cutoff, and every cue's
  `.lines` wrapped to 2x42 (new `Segment.lines` field; `Word.joins_previous`
  fixes the "New York 'a" spacing bug via `transcript.render_words`).
  Advisory QC now runs on source cues too (`qc.source_readability`/
  `qc.source_output`, never affects `valid`/`needs_review_count`).
  Measured on S01E01 (`benchmark-results/segmentation-naturalness-after-step2-2026-09-28.json`
  vs the baseline above): over-box cues 23.1% -> 18.2% (line wrap is
  working), turn recall 50.0% -> 58.3%, but boundary F1 63.5% -> 61.9%
  (precision fell as cue count rose 2035 -> 2419, further past the human
  1863) and mid-sentence-ending cues 12.8% -> 18.8%. That last pair is a
  real, disclosed tradeoff, not a bug: sentence-splitting with no minimum
  length means more, shorter cues, and a genuine mid-sentence acoustic
  pause (a real hesitation) now surfaces as its own short cue instead of
  being silently absorbed into a bigger blob by the old flat char gate --
  BoundaryReason.REAL_ACOUSTIC_GAP is deliberately never merged across
  (see the orphan-context-padding entry below; the same invariant
  translate.build_context_spans() relies on), so this is not something to
  "fix" by re-merging across real pauses. If cue count vs. human proves
  to matter in practice, the right lever is turn detection (step 3) and/or
  a readability-only merge pass that never touches BoundaryReason
  semantics -- not loosening the acoustic-gap rule.
- **Zero-duration source cue: real bug, found by the new source QC on the
  first real production job after deploying the above (S02E02, 2026-09-28,
  job `3431732d9c6d41aab31f40022aa692f1`)**: `qc.source_output` (new this
  session) flagged 3 cues with non-positive duration -- e.g. `"Tamam mı?"`
  at `3698.31 --> 3698.31`. Root cause: faster-whisper occasionally emits
  a word with `start == end` (a genuine zero-width timestamp, not a bug
  in this codebase), and a single-word cue built from exactly that word
  inherited the zero duration -- invisible before this session, since no
  QC had ever checked source cues at all. Fixed at the one place every
  downstream consumer benefits (`asr.segments_from_raw`,
  `asr.MIN_WORD_DURATION` = 0.01s floor): no `Word` is ever constructed
  with `end <= start`. `asr.PIPELINE_VERSION` bumped 2.0.0 -> 2.0.1 so
  the transcript cache key changes and no stale cached transcript (made
  before this fix) can be silently reused with the old zero-width words
  still in it.
- **`wrap_lines()` crash on an unspaced token, real production failure
  (2026-09-28, Hammer Session! (2010) S01E01, job `f6cc6c...`)**:
  `ValueError: min() iterable argument is empty` in
  `text_segmentation.wrap_lines()`, thrown from `segmentation_source.build_cues`
  on a real user-added episode. Cause: `wrap_lines()` (ported from
  `segmentation_target.py`) calls `text.split()` and, if no split point
  keeps both halves under budget, falls back to `min(range(1, len(words)), ...)`
  -- but if `text` is a single token with NO spaces at all (garbled/
  unusual ASR output, a URL, ...) longer than `MAX_LINE_CHARS`,
  `len(words) == 1` and `range(1, 1)` is empty, so `min()` crashes on an
  empty iterable. `split_long_piece()` right above it already guards this
  exact shape (`if len(words) < 2: return [piece]`); `wrap_lines()`
  didn't have the matching guard. Never triggered in
  `segmentation_target.py` (translated English rarely produces one
  60+-char unspaced token), but `segmentation_source.py` calls the same
  function on SOURCE text in any language, where it's a real, reachable
  case -- fixed in both `text_segmentation.py` (the copy actually in the
  crash path) and the original in `segmentation_target.py` (same latent
  bug, same fix, before it gets its own real-world trigger).
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
- Only pass `<remote-host>` to `./scripts/deploy.sh` when a remote
  translate-server actually exists (none does today -- see "Host" above)
  and the change touches `translate.py`/`translate_server.py`; everything
  else (API, worker, scratch cleanup, frontend) only needs the plain
  local deploy, which is the default.

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
  and commit there too; this repo's git history won't show it. Web-UI
  edits (promote/update/delete) commit themselves since 2026-09-28
  (`glossary_files.py`, author "subtitle-ai web UI"), and are written
  with ruamel.yaml round-trip so comments survive. Before that, a UI
  promotion's `yaml.safe_dump()` silently deleted every evidence comment
  in `love-is-in-the-air.yaml` (restored in that repo's history). The
  comments ARE the evidence record for the "evidence bar" section below
  -- never write these files with plain pyyaml.
- **Job database** (`${CONFIG_PATH}/subtitle-ai/cache/jobs.db`,
  SQLite/WAL): backed up daily at 03:00 by a host crontab entry running
  `scripts/backup_jobs_db.sh` (safe against the live DB; 14-day
  retention in `${CONFIG_PATH}/subtitle-ai/backups/`). The crontab is
  per-host state -- it silently did NOT survive the 2026-09-27 migration
  and was reinstalled 2026-09-28; see "Host setup checklist" above.

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

The same discipline applies in the other direction: any rule emitted at
>=0.7 feeds `needs_review` on every job. Until 2026-09-28 readability's
min-duration rule sat at exactly 0.7 and was 99.97% of all
`needs_review` hits in production (62,490 of 62,512), burying the ~21
real entity/hallucination findings; it and max-duration now emit at
`readability_qc.DURATION_CONFIDENCE` (0.5) -- timing isn't fixable in the
text-only editor, and Workflow B inherits it from the source SRT anyway.
Stored rows were recounted with `scripts/recompute_needs_review.py`
(20,058 -> 22). Before raising any rule to >=0.7, count how often it
fires across the real job database first.

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
translation protection directly -- promoting a MINED name to `protected:
true` is always a deliberate human/session decision. The one automatic
path is `cast_enrichment.py` (next section), which enforces this same bar
in code -- credited by cast metadata, recurring as a name, and a measured
mistranslation -- and scopes what it protects to credited episodes.

## Cast metadata: TVDB + TMDB + IMDb feed name protection (since 2026-09-28)

`cast_metadata.py` pulls per-episode character credits from TMDB
(`TMDB_API_KEY`), TheTVDB (`tvdb_client.episode_characters()`,
`TVDB_API_KEY`), IMDb's free non-commercial datasets (no key -- the
official IMDb API is paid and scraping imdb.com is against its terms) and
the series' `tvshow.nfo`, merged on the diacritic-folded first name
(services disagree on surnames). `cast_enrichment.py` then applies the
evidence bar below automatically: a credited name is protected only if the
series' own source subtitles use it *as a name* and the production engine
demonstrably loses it unprotected. What passes is written as a
`source: metadata`, `case_sensitive: true`, episode-scoped entry with its
evidence and committed to the glossary repo; entries a person wrote are
never edited (the report only flags them). The worker re-checks each
series every `SUBTITLE_AI_CAST_REFRESH_DAYS` (30) while idle;
`scripts/refresh_cast.py` runs it on demand. First run (Love Is In The
Air): 8 names protected, each with real mistranslations behind it (Kiraz ->
"Cherry" 30/30 lines, Balca -> "The hammer", Melek -> "The angel", Sevda ->
"Love"), while names that translate fine unprotected (Ayfer, Semiha, ...)
were left alone.

Two glossary features exist because of this and apply to any entry:
`episodes: ["S01E29-E40", ...]` (the job's SxxEyy decides; an unknown
episode gets no scoped names) and `case_sensitive: true`. Deniz is the
reference case: "sea" in S01E01-E28, a character from E29 -- see the
comment on its entry in `love-is-in-the-air.yaml`.

Known limit: a capitalised common word inside a scoped name's episodes is
still protected (e.g. the pun "O yanındaki Melek değil, şeytan", angel vs
the character Melek). The evidence gate keeps such names few; don't widen
`name_shaped()` to count sentence-initial capitals as names.

**Known limit: the evidence gate only works for Latin-script source
languages** (confirmed 2026-09-28, "Hammer Session!" {tvdb-177461}, a
Japanese drama). `cast_enrichment.name_shaped()` searches for the
candidate's ROMANIZED name (from TMDB/TVDB/IMDb, e.g. "Tachibana") as a
literal substring of the source subtitle TEXT -- for a Japanese `.ja.srt`
(kanji/kana), that string can never appear, so every candidate reports
`used as a name in 0 lines / 0 episodes` and nothing is ever protected,
regardless of how real or well-credited the cast is. Confirmed the DATA
side works fine (`scripts/refresh_cast.py`, no `--dry-run`, pulled 25 real
named cast members from TMDB/IMDb/TVmaze/the local `.nfo` correctly) --
this is specifically the evidence-matching step, not an import failure.
Not fixed this session (would need matching against the actual script the
candidate's real name is written in, which TMDB/TVDB don't reliably
supply in kanji) -- flagged here so a future session doesn't waste time
re-diagnosing "why does cast metadata never protect anything for this
series" from scratch.

## ASR initial_prompt style experiment: measured, NOT adopted (2026-09-28)

Natural-dialogue plan step 5 (gated). `AsrConfig.initial_prompt`
(`asr.py`, `SUBTITLE_AI_ASR_STYLE=natural`, off by default) feeds Whisper
a short sample of ordinary sentence-case Turkish dialogue with
interjections before decoding, hoping the decoder would lean toward
keeping genuinely-spoken interjections ("Aa!", "Of ya!") it otherwise
drops -- part of `_model_info()` so it's covered by the transcript cache
key. Measured on S01E01, real GPU run
(`benchmark-results/asr-style-initial-prompt-2026-09-28.json`,
`asr:style=natural` vs. the production-default `cache` baseline via
`eval_transcription.py --segmentation`):

- WER 16.7% -> 16.4% (95% CI [-1.10, +0.00] -- not worse, borderline better)
- name recall 85.4% -> 85.4% (unchanged)
- no Title Case surge (26.3% capitalised-first-letter words, ordinary
  sentence/proper-noun capitalisation -- nowhere near the ~65-75% the
  hotwords failure showed, this module's own docstring)
- **interjection recall 78.7% -> 78.7% of 89 -- exactly unchanged.**

Three of the four adoption criteria pass; the fourth -- the entire reason
for trying this -- shows zero effect. **Not adopted**: `initial_prompt`
biases decoding STYLE (punctuation/casing conventions), and dropped
interjections apparently aren't a style problem Whisper's decoder can be
nudged out of this way; they're being dropped somewhere further upstream
(VAD, or the decoder simply not "hearing" a very short vocalisation as a
word at all). Code, `SUBTITLE_AI_ASR_STYLE` toggle, and the eval
integration (`asr:style=natural`) are shipped -- genuinely useful if this
gets revisited with a different theory of the drop, e.g. trying VAD
parameters tuned specifically for short interjections rather than a
prompt -- but the setting stays off; there is nothing here to turn on.

## Speaker-turn detection: implemented, measured, shipped OFF (2026-09-28)

`subtitle_ai/turns.py` -- `SUBTITLE_AI_TURN_DETECTION=heuristic|voice|off`
(default **off**), natural-dialogue plan step 3. Two detectors behind one
`detect_turns(words, wav_path=None)` interface, each returning word
indices where a new speaker's turn starts (becomes a
`BoundaryReason.UTTERANCE_END` boundary once wired into segmentation,
which `translate.build_context_spans()` already treats as a real break --
see transcript.py):

- **heuristic**: sentence-end pause, question-implies-reply, or a short
  reply, each requiring at least a small real pause (no zero-gap trigger
  -- an early cut without that fired on 1 word in 6, see below).
- **voice**: WeSpeaker ResNet34 speaker-embedding model, ONNX
  (`Wespeaker/wespeaker-voxceleb-resnet34-LM` on Hugging Face, public, no
  token -- `scripts/download_speaker_model.py`), cosine similarity
  between consecutive sentence-chunk embeddings. Feature extraction is a
  pure-numpy log-mel-filterbank approximation of Kaldi's fbank (no
  torchaudio/scipy/librosa in this environment) -- NOT verified bit-exact
  against a reference implementation; check this first if voice-detector
  quality ever looks suspiciously low.

Measured on S01E01 (`benchmark-results/turn-detection-comparison-2026-09-28.json`,
`scripts/compare_turn_detectors.py`): heuristic's first cut fired on 1606
of 10025 words (16%, recall 75% / precision 0.6% against the available
ground truth); retuned (require a real pause for the weaker signals),
1093 words (10.9%, recall 33.3%). Voice: 1654 words (16.5%, recall 41.7%
/ precision 0.3%), 110.5s of CPU inference for one episode. **Ground
truth caveat**: only 12 true turn points exist for the whole episode --
`eval_transcription.dash_turn_points()` only sees a speaker change when
the human subtitle renders it as ONE two-line "- A / - B" cue; most real
speaker changes are just consecutive ordinary cues, invisible to this
measurement. Precision against 12 points is not a trustworthy number at
all; recall is a weak signal but the best available.

**Decision**: default is `off`. Both detectors over-trigger far past
anything usable in production (>10% of words as a turn boundary would
fragment translation context much more than the natural-dialogue plan
intends), and the ground truth can't validate precision well enough to
trust a tuned threshold. Heuristic is clearly the better of the two on
the numbers that ARE meaningful (recall, and instant vs. 110s/episode),
so it's the one to reach for if this gets revisited -- but neither
cleared the bar to ship as a default. The code, tests, and env-var toggle
exist so this can be picked back up with better ground truth (e.g. a
small hand-annotated clip) without redoing the implementation.

## Sonarr / Radarr / Plex / Jellyfin (since 2026-09-28)

All four see the library at the same `/data/media/...` paths as this app
(verified live), so nothing translates paths.

- **`arr_client.py`**: Sonarr/Radarr decide which series or movie a file
  is (longest matching folder), with its TVDB/TMDB/IMDb/TVmaze ids and
  original language. `glossary_profile.find_tvdb_id()` asks Sonarr FIRST
  and falls back to the `{tvdb-<id>}` tag, because the tag is wrong in
  practice: 3 of 1,984 series have a broken tag (`{tvbd-...}`, a stray
  space), and 6 disagree with Sonarr. New jobs for those 6 get Sonarr's
  id, so their old jobs (tagged id) and new ones show as two series. The
  index is served stale while it refreshes (Radarr's list takes ~8s) and is
  persisted in `/cache/arr`, so never put a blocking fetch back into
  `get()`.
- **Movies** have a glossary file keyed `tmdb_movie_id` and film-wide cast
  protection (TMDB movie credits). As of 2026-09-28 no movie in the library
  has a genuine original-language subtitle: every `.hi.srt` checked turned
  out to be English (hearing-impaired). That's why `source_subtitles()`
  checks a file's TEXT with langdetect rather than trusting its name.
- **`media_servers.py`**: after a job commits files, Plex gets a partial
  scan of that one folder and Jellyfin gets `/Library/Media/Updated`. This
  runs on a background thread, and the outcome goes to the job log
  (`MEDIA_SERVERS_NOTIFIED`). No thread starts when neither server is
  configured; this matters for tests, where a late log write raced the
  temp-dir cleanup.
- MDBList was considered and not used: it has ids and ratings but no
  character data, and Sonarr/Radarr already supply the ids.

## English span distribution: sentence-aware, not word-count-fraction (2026-09-28)

Natural-dialogue plan step 4. When one translation SPAN (`translate.build_context_spans`)
covers 2+ display GROUPS (`projection.merge_groups` -- happens whenever a
`SENTENCE_END` boundary sits between them, since that boundary isn't in
`MERGEABLE_BOUNDARIES` but also doesn't end the translation span), the
one translated string has to be re-divided back across those groups.
`pipeline._distribute_span_text`/`_pack_pieces_by_weight` replaced a raw
word-count-FRACTION cut with one that prefers to cut exactly BETWEEN
sentences (or between dash-formatted speaker lines, reusing
`glossary.split_multi_speaker_dash_lines`), never inside one, and always
gives every group at least one non-empty word (`projection.validate_coverage`
hard-fails a group with zero cues).

Caught and fixed on a real S01E01 run before shipping: NLLB doesn't
reliably preserve sentence COUNT (two short source sentences often become
one fluent English sentence), so an earlier version of this fix back-filled
every group that got no sentence of its own with the FULL span text --
correct for a single empty group in isolation, but visibly wrong once two
adjacent groups both did it (duplicate consecutive English cues, e.g. "It's
a gift full of surprises..." shown twice in a row). Fixed by splitting a
SHARED sentence at the word level across just the groups that need to
share it, instead of duplicating it whole; true duplication is now only
the last resort when a span has fewer WORDS than groups (a handful of
real, legitimate cases exist too -- e.g. "Bekle." said 4 times in a row
as 4 separate real cues, which NLLB embellishes identically each time to
"Wait, wait, wait, whoa." -- not a distribution bug, just short-utterance
translation behaviour; a scan of the real S01E01 output found 23/2240
duplicate-consecutive-text cues after this fix, effectively all of this
shape rather than the redistribution bug).

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

## VAD-merged singing+dialogue: recovered, not just flagged (2026-09-28)

Real user report, not a hypothesis: Hammer Session! S01E02, 0:20-2:14 (the
OP), contains both singing AND spoken dialogue. Production transcribed
NONE of it -- zero segments in that 90-second span, not even suppressed
ones. Root cause, confirmed by isolating that exact window
(`ffmpeg -ss -t` clip) and re-transcribing it directly: with VAD on
(production default), faster-whisper's Silero VAD never sees a silence
gap long enough to clear `min_silence_duration_ms` across that span (the
song bridges every pause between spoken lines), so it hands the decoder
one continuous ~95-second "speech island." A single uninterrupted span
that long decodes very poorly -- one segment, single-digit characters of
text for the whole span. With VAD off on the same clip, 4 segments came
back spread across the same span, each with real content.

Fix (`asr.recover_vad_merged_segments`, called from `asr.transcribe`
whenever `vad_filter` is on): any decoded segment >= `VAD_MERGE_MIN_DURATION`
(12s) with <= `VAD_MERGE_MAX_DENSITY` (2.5) characters/second of text is
re-decoded in isolation with VAD off (`condition_on_previous_text=False`,
same as the main pass); the replacement is kept only if it holds MORE
text than the original, so a legitimately sparse-but-correct segment
(long silence, one trailing word) is never made worse. This is a
correctness recovery, not a style change -- normal dense dialogue never
matches the shape (measured: a real 20-second monologue segment runs
tens of chars/second, an order of magnitude above the threshold) and is
never touched. Part of the transcript cache key
(`AsrConfig.vad_merge_recovery`, `_model_info`), and `PIPELINE_VERSION`
bumped to `2.0.2` to invalidate every cached transcript made before this
existed -- cached transcripts hide this bug identically to the
zero-duration-word bug above; they must be regenerated, not reused.
Unit tests: `tests/test_vad_merge_recovery.py`.
