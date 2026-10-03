# Decision: Re-time an existing subtitle against the audio (2026-10-03)

Status: Accepted. Library module, command-line tool, queued job type
`subtitle_retime` and `POST /api/subtitle-retimes`.

## Context

A human subtitle in the video's own language can have the right text and the
wrong times: a different release, a frame-rate mismatch, a cut or ad break
(`subtitle-timing.md`, hypotheses). Workflow B keeps the source cue times, so it
carries that error into its English output; Workflow A would discard the human
wording.

## Decision

`subtitle_ai/retime.py` and `scripts/retime_subtitle.py` move each cue to where
its words are spoken and change nothing else.

- The transcript is made from audio alone with production settings (a cached
  transcript of the video is reused). The subtitle is compared with it afterwards;
  it never reaches ASR and never repairs the transcript. Cue text and line breaks
  are written back unchanged.
- This is neither Workflow A nor B and shares no code path with them, so the
  "never mix them" rule holds.
- The correction is not a constant. It is a piecewise-linear function of time
  estimated for this file: a shift, a frame-rate stretch (linear drift) or steps,
  reported with its evidence. "Never add a magic timing offset" holds because the
  offset comes from this file's audio and is shown, not assumed.
- Method: runs of 3 identical words (6 characters for ja/zh/th) shared by subtitle
  and transcript anchor a cue; its offset is the median over its matched tokens.
  Anchors that disagree with both neighbours are dropped; a robust line is fitted
  and split at the largest step while 90% of anchors are not within 0.7 s of it.
- Refusals, nothing written (exit 2): fewer than 20 anchored cues or under 25% of
  cues; more than 20% of anchors disagreeing with their neighbours; a 95th
  percentile residual over 1.0 s after fitting. The wrong episode or the wrong
  language lands here.
- A subtitle already within 0.25 s of the audio is left alone.
- Output is written atomically through `output.write_srt_atomic`. The input is
  untouched unless `--in-place`; protected names (`.en.hi.srt` etc.) are never
  written.

## Evidence

Love Is In The Air S01E01-E04: the human Turkish subtitle, shifted by a known
function, retimed against the production transcript, scored against the human
subtitle's original times (mean of start and end error, seconds):

| Distortion | Detected as | E01 | E02 | E03 | E04 |
|---|---|---|---|---|---|
| none | aligned (nothing written) | 0.00 | 0.00 | 0.00 | 0.00 |
| +5.1 s | constant offset | 0.06 | 0.16 | 0.16 | 0.11 |
| -7 s | constant offset | 0.06 | 0.16 | 0.16 | 0.11 |
| 25 -> 23.976 fps | linear drift, 1 piece | 0.05 | 0.16 | 0.18 | 0.14 |
| +4 s after 40 min | stepped, 2 pieces | 0.10 | 0.17 | 0.16 | 0.13 |
| +30 s at 20 min, +18 s at 70 min | stepped, 3-4 pieces | 0.22 | 0.33 | 0.20 | 0.17 |

1,553-1,615 cues anchored per episode (about 77% of 1,900-2,100); a run takes
under a second. Wrong episode: 21 of 2,039 cues anchored, refused.

What the error contains: a human timer and word timestamps from ASR disagree by
about 0.15 s (the earlier S01 measurement was -0.12 to -0.18 s), so the result
lines up with the audio's word timing, not with the human's convention. The
residual of a correct single line is 0.14-0.16 s median and 0.47-0.55 s at the
95th percentile; its 90th percentile is 0.38-0.44 s, which is why
`FIT_TOLERANCE` is 0.7 s (at 0.4 s noise split segments and a frame-rate drift
came back as 18-53 pieces).

## Not measured

- Languages other than Turkish. Unspaced languages use character runs; the
  6-character minimum is a judgement, not a measurement.
- A real mismatched pair (the reported ~5.1 s one is still missing). The
  distortions above are synthetic, applied to a real subtitle.
- Steps shorter than 0.7 s are not split; the error is then up to half the step.
- Subtitles whose text differs a lot from what is said (a heavily condensed or
  edited track) anchor fewer cues and are refused below 25%.
- Songs and untranscribed passages carry the offset of the nearest evidence.

## Validation

`tests/test_retime.py` (synthetic programme with a known truth: shift early and
late, frame rate, one and two steps, noise, 7% wrong text, wrong programme,
contradictory evidence, no overlap after a step, tokenisation) and
`tests/test_retime_subtitle_script.py` (text and line breaks preserved, input
untouched, `--in-place`, `--dry-run`, aligned left alone, refusal, protected
name). Full suite 1,389 passed; ruff and mypy clean.

## As a job

- `POST /api/subtitle-retimes` queues `job_type` `subtitle_retime`; the worker runs
  `retime_job.run_retime` in a scratch directory and commits with one atomic write.
  Adds a job type and the error code `RETIME_REFUSED` (add-only contract changes).
- Default output is the sidecar `<stem>.<language>.retimed.srt`, so no existing
  subtitle is touched; `replace_original` writes `<stem>.<language>.srt` instead.
  The destination is chosen when the job is created and is always overwritable, so
  re-running replaces the previous retimed copy.
- A protected external subtitle (`.en.hi.srt` etc.) is refused as a source.
- The transcript comes from the transcript cache when the video has one, else the
  audio is transcribed on the GPU (about 8 minutes for a 2 hour episode). Nothing
  is written to the cache.
- A subtitle already in step with the audio completes with no output.
- The subtitle's language is required (it decides per-character matching); it is
  read from the file name (`film.tr.srt`) when not given.
