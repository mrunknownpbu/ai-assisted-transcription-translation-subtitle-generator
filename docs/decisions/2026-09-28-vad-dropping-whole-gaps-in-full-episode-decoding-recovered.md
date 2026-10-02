# VAD dropping whole gaps in full-episode decoding: recovered (2026-09-28)

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
