# English span distribution: sentence-aware, not word-count-fraction (2026-09-28)

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
