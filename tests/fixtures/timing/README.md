# Real timing reference pairs

Permanent regression fixtures for subtitle timing. Each case is a **real**
human reference subtitle and the **real** subtitle this pipeline produced for
the same video. Never add a synthetic or hand-edited pair here: synthetic
cases belong in `tests/srt_offset_fixtures.py`, and they are stand-ins, not
evidence.

The reported ~5.1 s human/AI offset (docs/handover.md,
docs/decisions/subtitle-timing.md) has no real pair in this checkout yet, so
`manifest.json` has no cases and `tests/test_timing_reference_pairs.py` skips.

## Adding the real pair

1. Copy the two files here, e.g. `show-s01e01.reference.srt` (human) and
   `show-s01e01.candidate.srt` (this pipeline's output). Check that you may
   keep the human subtitle in the repository; if not, keep it out of git and
   record only the measured result.
2. Run the evaluator and read the verdict first:

   ```bash
   python scripts/eval_srt_quality.py tests/fixtures/timing/show-s01e01.reference.srt \
       tests/fixtures/timing/show-s01e01.candidate.srt
   ```

3. Add an entry to `manifest.json` stating what you observed, not what you
   hope for:

   ```json
   {"cases": [{
     "name": "show-s01e01",
     "reference": "show-s01e01.reference.srt",
     "candidate": "show-s01e01.candidate.srt",
     "source": "who made the reference, which job made the candidate, when",
     "expected": {"classification": "constant_offset",
                  "median_time_offset": 5.1, "tolerance": 0.3}
   }]}
   ```

   `classification` is `constant_offset` or `drift_or_nonlinear`;
   `median_time_offset` (seconds, candidate minus reference) and `tolerance`
   are optional. When the pipeline is fixed, change `expected` to the corrected
   behaviour in the same commit as the fix; never loosen the tolerance to make
   a failing case pass.
4. Record the finding in `docs/decisions/subtitle-timing.md`.
