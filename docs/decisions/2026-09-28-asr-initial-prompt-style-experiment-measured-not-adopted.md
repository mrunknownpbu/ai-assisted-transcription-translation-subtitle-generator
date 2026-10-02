# ASR initial_prompt style experiment: measured, NOT adopted (2026-09-28)

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
