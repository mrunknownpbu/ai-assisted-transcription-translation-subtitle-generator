# Operations reference

Moved verbatim from `CLAUDE.md` (formerly "Scratch dirs and the remote translate-server (ops behavior)"). Read it before touching workdir cleanup, the remote translate-server, GPU preflight or the job lifecycle.


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
  grounding, not a segmentation change** (2026-09-23, docs/IMPROVEMENT_PLAN.md
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
