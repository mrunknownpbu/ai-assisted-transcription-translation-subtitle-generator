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

## VAD dropping whole gaps in full-episode decoding: recovered (2026-09-28)

Real user report, not a hypothesis: Hammer Session! S01E02, 0:20-2:14 (the
OP), contains both singing AND spoken dialogue. Production transcribed
NONE of it -- zero segments in that 90-second span, not even suppressed
ones. Three attempts before the real fix, each disproved by re-running
against this exact case (not just re-reasoning about it):

1. **Segment-density recovery** (2.0.2): treated an existing long+sparse
   segment as the pathological shape and re-decoded it. Wrong -- there
   was no segment there at all to match, so this never fired; a
   redeployed re-run left the gap exactly as empty as before.
2. **Gap-detection + VAD-on retry** (2.0.3): correctly found the *gap*
   (no segment covers 10.2s-100.1s) but re-decoded it with VAD kept ON.
   Still produced nothing. Root cause found here: `word_timestamps=True`
   (required throughout this pipeline) does its own alignment pass on
   top of whatever VAD hands the decoder, and that alignment collapses
   on an unbroken multi-minute span -- keeps the first ~5 seconds of
   text, then jumps straight to the next real VAD boundary, silently
   dropping everything between. Confirmed by decoding the isolated
   clip both ways side by side: `word_timestamps=True` + VAD on =
   1 segment (0.0-5.2s) then a 90-second silent jump; `word_timestamps=False`
   + VAD on = 1 segment spanning the WHOLE clip with real text throughout
   (word alignment is exactly the thing that was breaking).
3. **Gap-detection + VAD-off retry** (2.0.4, shipped): re-decoding the
   isolated gap with VAD off keeps `word_timestamps=True` from ever
   seeing one giant span in the first place -- confirmed directly,
   13 normal few-second segments with real text across the whole
   90-second gap, word timestamps intact.

Fix (`asr.recover_vad_merged_segments`, called from `asr.transcribe`
whenever `vad_filter` is on): after the main decode, any stretch of the
file `GAP_MIN_DURATION` (15s) or longer that no decoded segment covers
(mid-file, or trailing to `total_duration`) is re-decoded in isolation
with `_extract_wav_window` + a fresh `model.transcribe()` call, VAD off,
`condition_on_previous_text=False`. Disabling VAD for this retry does
give up VAD's own protection against hallucinating on a genuinely silent
window (a real instrumental-only stretch, a scene transition) --
accepted because `hallucination.py` still runs over every segment this
pass adds, exactly like any other segment; it is not exempted from the
usual quality gate, just no longer gated a second time by VAD before
reaching it. Part of the transcript cache key (`AsrConfig.vad_merge_recovery`,
`_model_info`); `PIPELINE_VERSION` bumped three times across the three
attempts (2.0.2, 2.0.3, 2.0.4) -- each wrong fix's own cache entries had
to be invalidated too, not just the pre-fix ones, or a same-cache-key
retest silently serves the stale broken transcript back and looks like a
successful fresh re-run (this is what caught attempt 1 being wrong).
Cached transcripts hide this bug identically to the zero-duration-word
bug above; they must be regenerated, not reused. Unit tests:
`tests/test_vad_merge_recovery.py`. Lesson: "measure first" means
measure the FIX against the real failing case before declaring it done
and moving to the next step, not just the plausible mechanism -- and
remember to bump the cache key every time the fix's own logic changes,
not just once per feature.

**4th attempt (2.0.5, 2026-09-29): the collapse also has a second
shape.** Built `scripts/eval_against_human_en_reference.py` to check gap
coverage against a season's human `.en.hi.srt` systematically instead of
by hand, and ran it on S01E01 (756 human cues): 18.8% had zero
overlapping source-language coverage -- far more than one OP song
explains. Investigating the worst offenders found the same
`word_timestamps=True` alignment collapse can also leave ONE segment
nominally covering the span instead of no segment at all: a real
11.6-50.5s (38.9s) segment holding only ~14 characters, where a human
subtitle has a full line ("Hot, isn't it? It's so hot!..."). Confirmed
the same way as the gap case -- VAD kept ON for an isolated re-decode of
just that span reproduces the identical collapse; VAD off recovers 6
normal segments with real text. `_find_long_gaps` alone never sees this
shape (there IS a segment there). Added `_is_long_and_sparse` (same
`GAP_MIN_DURATION`, new `SPARSE_MAX_DENSITY` = 2.5 chars/sec) alongside
gap detection in `recover_vad_merged_segments`; a sparse segment's
VAD-off retry replaces it only if it recovers MORE text, same
keep-if-not-worse rule as before. Verified on the real case: gap-coverage
rate on S01E01 dropped 18.8% -> 13.4% (142 -> 101 of 756 human cues),
and the "Hot, isn't it" line specifically is now covered.
`PIPELINE_VERSION` bumped again (2.0.5). The remaining 13.4% still needs
its own investigation (a spot check found more genuine misses plus at
least one repetitive-hallucination case Whisper's own decoder-native
mitigation and `hallucination.py` both missed -- "Oh, oh, my God, oh,
Oh, oh my God, Oh, my God." replacing real dialogue -- not yet
addressed). Separately, `eval_against_human_en_reference.py` also scores
the production `.en.srt` against the human reference with `eval_translation.py`'s
chrF/`pair_cues`: on S01E01 this reads low (~22-23) but a manual check of
the actual worst-scoring pairs shows it is dominated by a cue-granularity/
timing-pairing mismatch (our cues split mid-sentence far more than a
human editor would, so the time-overlap pairing grabs the wrong fragment
against the wrong human cue) rather than genuinely bad translation --
the best-scoring pairs, where timing does line up, are close paraphrases.
Not yet investigated further.

**Open, measurement tooling only (2026-09-29): two problems found by the
S01E01 human-reference comparison, not yet fixed.**

1. *A compression_ratio=51 loop survives.* S01E01 3124.7-3150.1s (25.4s,
   transcript cache, pipeline 2.0.5) decoded as "ー" repeated ~80 times
   where the human subtitle has real lines ("It's dangerous to have such
   kind of things." / "Damn it."). compression_ratio=51.46,
   no_speech_prob=0.32, avg_logprob=-0.053 -> hallucination_score=0.5, not
   suppressed: `score_segment()` scores compression_ratio as a step (any
   value >= 2.4 adds exactly 0.5), and a repetition loop's decoder is
   confident, so nothing else fires. Before making that term graduated,
   measure the real distribution: `scripts/hallucination_signal_distribution.py`
   (percentiles over every cached segment, loop-shaped and suppressed
   subsets, every segment above a ratio listed with its text, and
   `--what-if-span S` listing exactly which segments a candidate
   `0.5 + 0.5 * (ratio - 2.4) / S` would newly suppress -- read that list
   for real dialogue before choosing S).
2. *Cues split mid-sentence far more than a human editor's.* chrF 22.43
   over 361 time-paired cues; e.g. ours "Hey, can" / "you" / "read it?"
   (1881.2-1895.6s) against one human cue. `eval_against_human_en_reference.py`
   now reports cue granularity for both our source-language and our English
   cues (`fragmentation()`: cues per human cue, share of human cues split,
   short-cue and mid-sentence-end rates, mean duration/chars), so a split
   made in `segmentation_source.build_cues` can be told apart from one
   added by the English span distribution in `pipeline.py`; `--worst N`
   prints the lowest-chrF pairs. Baseline (S01E01, taken 2026-09-29 after
   the fix below): source cues 1.087 per human cue (6.6% of human cues
   split), mid-sentence-end rate 0.873 (this counts Japanese source cues
   against an English sentence-ending regex -- likely inflated by
   Japanese punctuation conventions, not yet checked); English cues 1.114
   per human cue (9.3% split), mid-sentence-end rate 0.13. Not yet
   changed -- this is a baseline to measure a fix against, not a
   conclusion about which stage (source segmentation vs. English span
   distribution) is more responsible.

**Problem 1 fixed (2026-09-29): compression_ratio is now graduated.**
`scripts/hallucination_signal_distribution.py` measured the real
distribution across the WHOLE library (33 transcripts, 50815 segments):
p99.9 compression_ratio is 2.12 (comfortably under the 2.4 threshold
itself), and exactly ONE segment in the entire library ever crosses 2.4
at all -- the case above. Zero real risk of a graduated score newly
suppressing legitimate content, based on actual evidence rather than a
guess. `hallucination.compression_ratio_score(ratio)` replaces the step
(`score = max(score, 0.5)` for any ratio >= threshold) with
`min(1.0, 0.5 + 0.5 * (ratio - 2.4) / COMPRESSION_RATIO_SPAN)`,
`COMPRESSION_RATIO_SPAN = 10.0` -- chosen so nothing observed between 2.4
and 7.4 (a ratio would need to reach 7.4 to hit `SUPPRESSION_THRESHOLD`
on this signal alone) exists in the measured library, while the real
51.46 case reaches the 1.0 cap outright. Verified on a real (cache-hit,
no GPU needed -- hallucination.detect() runs fresh every pipeline run
regardless of ASR cache) re-run of S01E01: the repetition-loop cue at
3124.7-3150.1s is gone from `.ja.srt`. Unit tests:
`tests/test_hallucination.py` (`CompressionRatioScoreTests`, the real
case, and a borderline-ratio-alone-still-doesn't-suppress regression
guard). Gap-coverage/chrF on S01E01 barely moved after this fix (13.4%
-> 13.8% uncovered, chrF 22.43 -> 22.59) -- expected: suppressing one
wrongly-covering hallucinated segment turns that span into an honest gap
rather than removing a gap, noise-level movement either way, not a
regression.

**Problem 2 fixed (2026-09-29): English span distribution merges display
groups instead of cutting a sentence at the word level.** Root cause,
found by the Master code review session: `_pack_pieces_by_weight` was
built for "N sentences over N groups" and "fewer groups than sentences";
when a translation span covered MORE display groups than NLLB returned
sentences, it fell back to cutting a sentence at the word level so every
group got a slice -- the literal cause of "Hey, can" / "you" / "read it?"
(S01E01 1881.2-1895.6s, ~14s of Japanese with no real pause, cut into 3
groups by `segmentation_source.MAX_DURATION` that `merge_groups()`
couldn't rejoin since its own envelope check caps at 7s, all inside one
translation span; NLLB returned one sentence for it). A second,
compounding bug: `_group_weight` used `len(text.split())` for a weight,
which is 1 for any unspaced-language (Japanese) cue regardless of its
real length (`transcript.NO_SPACE_LANGUAGES`) -- every group weighed the
same, so the word-level cut had no signal to work from either.

Fix: `_distribute_span_text` now returns runs `(first, stop, text)` over
group positions instead of one piece per group; when there are fewer
sentences than groups, `_merge_groups_into_sentences` picks run
boundaries by matching cumulative group weight against cumulative
sentence-character fraction, and adjacent groups in a run are merged into
ONE display window -- always safe, since every group in a run is already
inside the same translation span (`build_context_spans` breaks a span at
`REAL_ACOUSTIC_GAP`/`UTTERANCE_END`, never crossed here). `_group_weight`
now counts characters for `NO_SPACE_LANGUAGES`, words otherwise. Verified
against the real case (cache-hit re-run, no GPU needed -- this only
touches the translation-distribution stage): S01E01 1881.2-1895.6s is now
one cue, "Hey, can you read it?". Season-wide-relevant metrics on S01E01
(`scripts/eval_against_human_en_reference.py`): English
`mid_sentence_end_rate` 0.13 -> 0.092, chrF vs the human reference 22.59
-> 24.34; gap-coverage unchanged (13.8%, expected -- this fix targets
fragmentation, not the separate missing-content problem above). Unit
tests: `tests/test_pipeline_fragmentation.py` (real end-to-end case
through `pipeline.run()`), `tests/test_pipeline_span_distribution.py`
(`_distribute_span_text`, `_merge_groups_into_sentences`, `_group_weight`).
The source-side `mid_sentence_end_rate=0.873` from the baseline above is
unaffected by this fix (it only touches English distribution) and is
still unexplained -- possibly partly an artifact of `fragmentation()`'s
English-oriented sentence-ending regex against Japanese punctuation, not
yet checked.

**Japanese sentence punctuation and cue sizing fixed (2026-09-29).**
Measured before editing the real Hammer Session! S01E01 `.ja.srt` and
matching cached transcript: both contained 20 `。` marks and zero `？` or
`！`; the human-reference eval regex already recognizes these marks, but
all four pipeline sentence-end regexes did not. The existing 84-character
source cue cap also left 26/598 Japanese cues over 42 characters (max 63).
The pipeline now recognizes `。？！` and Japanese quote closers in
`segmentation_source`, `text_segmentation`, `segmentation_target`, and
`translate`; `text_segmentation` also treats `、` as a clause boundary.
Japanese source cues use a 42-character cap; other languages keep 84.
This is post-ASR, so no pipeline/cache-version bump was needed.

Verified via a real POST `/api/jobs` rerun of S01E01 with
`source_lang=ja`, audio stream 1 (the initial AUTO attempt misidentified
the audio as Korean and was discarded; its generated `.ko.srt` was
removed and the backed-up `.en.srt` restored before the correct run).
The final job completed with the Japanese ASR cache hit, writing `.ja.srt`
and `.en.srt`; pre-run copies of both outputs remain under
`/cache/verification-backups/20260929-japanese-sentence-boundaries/` and
the intermediate pre-cap outputs under
`/cache/verification-backups/20260929-japanese-sentence-boundaries-after-punctuation/`.
On S01E01, source cues changed 598 -> 628; source cues per human cue
1.087 -> 1.117, split-human-cue rate 6.6% -> 8.9%, and mid-sentence-end
rate 0.873 -> 0.872. Japanese cues over 42 characters dropped 26 -> 0
(max 63 -> 42). English cues changed 608 -> 610; English
mid-sentence-end rate stayed 0.092, and chrF improved 24.34 -> 24.61
(352 -> 353 scored pairs). Gap coverage was unchanged: 104/756 (13.8%).
No human Japanese reference exists for Hammer Session, so a Japanese
boundary-F1 score is not available; the separate Turkish LIITA E01
`eval_transcription.py --segmentation` check remained 61.9% before and
after.

The separate translation-span ownership measurement also now has a real
positive reproduction: after this change, Hammer Session! S01E01 has
0 orphan spans out of 526, while Love Is In The Air S01E01 has 73 out of
2119. This is the next independent fix; do not fold it into the Japanese
segmentation change.

**Problem 2 fixed (2026-09-29): display groups now stop at translation
span boundaries.** The measured LIITA S01E01 baseline had 73 spans with
no owning display group out of 2119, and 74 groups crossed a span
boundary. Seventy-three crossings followed a sentence-ended one-cue span
whose next cue had a mergeable `MAX_DURATION` boundary; one followed a
`MAX_SPAN_CHARS` break. `pipeline.run()` now passes the starts of all
subsequent context spans to `projection.merge_groups()` as forced group
breaks. Unit and end-to-end tests cover the case where a sentence-ending
span is followed by a mergeable display boundary and verify both
translations reach the target SRT.

Verified on the real LIITA S01E01 through POST `/api/jobs` with
`source_lang=tr`, audio stream 1. The job completed in 446s. The original
human `.tr.srt` was kept (SHA-256 unchanged); only `.en.srt` was replaced.
The old transcript cache was pipeline 2.0.0, so this job did a fresh ASR
run and produced a 2.0.5 cache entry. On that same new transcript, the
previous unbounded `merge_groups()` behavior would leave 74/2167 spans
orphaned across 77 crossing groups; the fix leaves 0/2167 orphaned and
zero groups crossing spans. The original cached-data baseline remains
73/2119. Hammer Session! S01E01 remains at 0/526.

The `.en.srt` timing-pair comparison against the LIITA human `.en.hi.srt`
changed from 54.38 chrF over 1755 pairs (2306 output cues) to 44.73 over
1410 pairs (2362 cues); the cached-ASR WER changed 16.7% -> 16.9%, and
segmentation boundary F1 changed 61.9% -> 61.6%. These before/after
translation metrics are not an apples-to-apples attribution to this fix:
the production run necessarily regenerated ASR because the old cache
predated pipeline 2.0.5. Preserve this as a measured comparison, not a
claim that the grouping change caused the chrF movement. Backups of the
pre-run human `.tr.srt` and `.en.srt` are in
`/cache/verification-backups/20260929-orphaned-translation-spans/`.

## Residual gaps and long repetition loops (2026-09-29)

Corrected the earlier short-sparse scan to read cached word `text` (not
the faster-whisper-only raw `word` key). Across the latest 23 transcripts,
78 segments were 10-15s long with at most 1 character/sec. Five occur in
Hammer Session! S01E01 and one in Love Is In The Air S01E01. In Hammer,
the 882.9-895.0s and 3192.2-3203.6s spans have internal word-timing holes
and overlap human-reference cues that had no source coverage. Added a
stricter 10-15s recovery threshold (1 character/sec); these spans are
re-decoded with VAD off and replace the original only when they yield
more text.

The same corrected scan found a separate, more severe residual case:
the latest-cache distribution had one segment at compression_ratio >=
7.4, Hammer S01E01 3669.1-3707.0s (14.87), repeating sushi-menu text over
human dialogue. A direct isolated VAD-off decode recovered 10 normal
segments (compression ratios 1.03-1.23), including dialogue throughout
the human-reference gap. Long segments at this ratio are now retried;
their original is replaced only if the retry is nonempty and every
recovered segment is below the configured compression-ratio threshold.
This is independent of the graduated hallucination score: it recovers
the underlying speech before post-ASR hallucination filtering.

Verified with a fresh production POST `/api/jobs` on S01E01 (manual
Japanese, audio stream 1), pipeline 2.0.7: the ASR cache missed, the
full run took 218.5s, recovered 863 segments, suppressed zero, passed
validation, and wrote both `.ja.srt` and `.en.srt` on a follow-up cache-hit
run (7.6s). Before/after on the backed-up library pair: source cues
628 -> 631; English cues 614 -> 614; uncovered human cues 104/756
(13.8%) -> 102/756 (13.5%); chrF 24.49 -> 24.49 over 366 pairs. Of the
104 formerly uncovered cues, 17 gained source coverage and 15 previously
covered cues lost it. Because the pipeline-version bump forced a fresh
full-episode ASR pass, this net change is confounded by ASR variation and
does not establish that the recovery alone improved total coverage.
The 882.9-895.0s and 3192.2-3203.6s cases now have source and English
cues; the repeated-text span now has dialogue rather than the suppressed
loop. Current pipeline-2.0.7 hallucination scan: p99.9 compression ratio
2.12, zero segments >=2.4, one loop-shaped segment at 1.84, and no new
suppression under `--what-if-span 10`.

The exact "Oh, oh, my God..." sequence was not present in the current or
backed-up S01E01 outputs; no generic repetition suppressor was added.
Remaining gap coverage is 13.5%. Backups taken before production jobs are
in `/cache/verification-backups/20260929-short-sparse-vad-recovery/`,
`20260929-short-sparse-and-loop-recovery/`, and
`20260929-item3-cache-publish/`.

## Remote translation retry fallback (2026-09-29)

`remote_translate_batch()` now rejects malformed replies unless each
remote chunk returns exactly one string per submitted sentence. The
input/output mappings use strict `zip()` so any internal count mismatch
fails visibly instead of silently truncating. In `translate_spans()`,
`remote_succeeded` is set only after the optional run-on chunk retry has
also succeeded; if that retry fails, the whole payload goes through the
existing local fallback instead of producing `None` translations. Added
regressions for short remote replies and for a successful primary remote
call followed by a failed retry; the latter confirms local translation
is called and its result is returned. Full suite: 1127 passed, 38
subtests. The production container has no `TRANSLATE_SERVER_URL`, so a
production job would exercise only local translation and cannot verify
this remote-only failure path; the isolated remote integration tests are
the available verification for this deployment.

## Worker loop exception containment (2026-09-29)

`Worker.run()` now logs and isolates failures from work-root sweeping,
job claiming, idle cast refresh, and job processing. If an exception
escapes `_process()` (including a database error while its own failure
handler tries `store.finish()`), the worker makes one logged attempt to
mark the job failed, logs any failure of that write separately, then
continues polling instead of terminating its daemon thread. Regression
tests reproduce a locked-database claim and a locked `finish()` during
error handling; in both cases the loop reaches a second claim. Targeted
worker tests: 73 passed. Full suite: 1129 passed, 38 subtests.

## Atomic SRT temp-file uniqueness (2026-09-29)

`write_srt_atomic()` now creates a randomized temporary file with
`tempfile.mkstemp()` in the target directory, so a stale `.tmp<PID>` left
by a crash cannot block every later write when uvicorn reuses PID 1.
The temp remains on the target filesystem for atomic replacement and is
removed in `finally`; output permissions remain 0644. A regression test
pre-creates the old fixed-name temp and confirms a write succeeds without
altering it. Full suite: 1131 passed, 38 subtests. Verified with a
production POST `/api/jobs` cache-hit rerun of Hammer Session! S01E01
(17.4s, validation passed): both `.ja.srt` and `.en.srt` were atomically
written, matched their pre-run backups byte-for-byte, retained mode 0644,
and left no temp files. Backups:
`/cache/verification-backups/20260929-atomic-srt-temp/`.

## SRT editor input and active-job guard (2026-09-29)

`PUT /api/jobs/{id}/srt` now rejects blank cue lines, embedded CR/LF, and
the SRT timing arrow (`-->`) in each submitted text line; these could
otherwise create extra cues or timing syntax when rendered. It also
returns 409 unless the addressed job is completed, so editing a retry
cannot race its output write. Regression tests confirm malformed edits
leave the subtitle file unchanged and that an active retry is not editable.
The API tests pass (144 tests, 2 subtests); the full suite passes (1134
tests, 40 subtests). Deployed and checked on the production API: PUT to
the active Hammer Session! S01E01 job returned 409, and a cue-injection
payload returned 422. The required POST job completed, but its pipeline
result was unsuitable for comparison: AUTO detected Korean at 39.9%
confidence despite audio inspection selecting Japanese at 95.5%, wrote
`.ko.srt`, and changed `.en.srt`. Restored `.en.srt` byte-for-byte from
the pre-run backup (SHA-256
`34aac0df09db4375a09aef6a57c9254f1b29b918121af274b60127f164d55a58`),
confirmed `.ja.srt` unchanged
(`5b967a999a0b422704d936e0f3dd4222730467eb86a4d76cbafa1844bf5f1665`),
and removed the generated `.ko.srt`, which was absent before the run. Backup:
`/cache/verification-backups/20260929-srt-editor-hardening/`.
