# Decision: Subtitle timing, the reported ~5.1 s offset, and how it is tested

Date: 2026-10-02

Status: Accepted (diagnostic tooling and tests). The reported offset itself is
unresolved: it needs the real reference pair.

## Context

Timing is a product requirement, not decoration. The pipeline distinguishes
several time bases: ASR word timestamps (faster-whisper, relative to the
extracted WAV), a source subtitle's cue times (Workflow B), the translated
segments' boundaries, the final cue times after segmentation and projection,
and any correction applied afterwards. No global offset is ever added to a
subtitle; the extracted WAV starts at the file's own time zero and cue times
are derived from word times.

## Problem

A ~5.1 second offset between a human subtitle and an AI subtitle was reported
(docs/handover.md, 2026-10-01). The pair it came from is not in this
repository, so the claim could not be reproduced.

## Evidence

All of it is from this checkout and the live library on 2026-10-02.

1. **The evaluator was unreliable for this question.** `scripts/eval_srt_quality.py`
   paired cues by time overlap. With segmentation that differs between the files
   and a shift larger than a cue, every cue pairs with the wrong neighbour: a
   synthetic 5.1 s shift read as a 2.5 s mean and "drift". It now aligns on
   shared word runs (>= 3 words) and uses cue pairing only when none exist.
   Five of seven new tests fail on the old evaluator.
2. **Its classifier was too strict for real data.** On real files the
   per-word offset spread is ~0.5-1 s from interpolation noise alone, so a clean
   timeline was labelled `drift_or_nonlinear`. Classification now compares the
   median offset of six equal-count windows (limit 0.5 s) and the fitted drift.
3. **The pipeline's timeline matches a human reference on real episodes.**
   Love Is In The Air S01E01-E04: the newest production cached transcript
   against the human Turkish subtitle gives a median offset of -0.12 to -0.18 s
   (candidate earlier), window-median range 0.04-0.10 s, drift <= 3e-5 s/s;
   all `constant_offset`. Data: `benchmark-results/timing-vs-human-reference-2026-10-02.json`.
   This is source-language ASR cues; the English output was not measured.
4. **Container start times are not the cause here.** `ffprobe` over the 36
   distinct videos processed by video jobs: one file has a non-zero start
   (audio at 0.062 s), none over 0.1 s.

## Hypotheses (not established)

- The human subtitle was timed to a different release or cut (intro, logo,
  commercial gaps), which appears as a constant or stepped offset against any
  correct transcript.
- A file whose container start time differs from the one measured above (e.g. a
  remux with a leading offset). The library sample argues against it but is 36
  files.
- Workflow B inherits its timing from the source subtitle, so an offset in that
  subtitle is carried through unchanged; it would not be an AI timing error.

## Experiments

- Synthetic reference/candidate generator with a known 5.1 s shift (identical
  cues, re-segmented cues with jitter, a negative shift, none, a shift that
  starts midway, linear drift) in `tests/srt_offset_fixtures.py`. These test the
  diagnostic; they are not evidence about the pipeline.
- Real-data comparison in Evidence 3 and 4.

## Rejected approaches

- **A constant correction.** Adding or subtracting a fixed offset to hide the
  difference is not accepted: there is no evidence the pipeline is wrong, and a
  magic number would be wrong for every file it did not come from.
- **Assuming the human subtitle is the truth.** The reference may itself be
  shifted; only audio can arbitrate, and that needs a person to listen.

## Decision

Pipeline timing is unchanged. The diagnostic is made trustworthy (Evidence 1
and 2) and a permanent slot is provided for the real pair:
`tests/fixtures/timing/` with a manifest, loader and an auto-skipping test
(`tests/test_timing_reference_pairs.py`). Its README says how to add the pair
and that a synthetic pair must never be added there.

## Consequences

- Until the real pair exists, "is there a 5.1 s offset" is unanswered. What is
  known: on four real episodes the pipeline's timeline agrees with a human one
  within 0.2 s.
- When the pair arrives, run `python scripts/eval_srt_quality.py reference.srt
  candidate.srt` first. A constant offset points at the reference's release or
  the media; a stepped one at a gap handled differently by VAD or the human cut;
  drift at a frame-rate mismatch. Add the case to the manifest with what was
  observed, then fix, then update `expected` in the same commit.

## Validation

- `tests/test_eval_srt_quality.py` (12 tests), `tests/test_timing_reference_pairs.py`.
- Window thresholds measured, not guessed: observed real window ranges
  0.04-0.10 s against a 0.5 s limit. The 0.001 s/s drift limit predates this work
  and is unvalidated (TBD: requires measurement on a frame-rate-mismatched pair).
