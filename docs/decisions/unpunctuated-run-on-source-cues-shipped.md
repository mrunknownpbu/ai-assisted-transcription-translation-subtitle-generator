# Unpunctuated run-on source cues: shipped

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
