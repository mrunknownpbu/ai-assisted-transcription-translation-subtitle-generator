# Speaker-turn detection: implemented, measured, shipped OFF (2026-09-28)

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
