# Readability vs. content-completeness: an accepted, disclosed tradeoff

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
