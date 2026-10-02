# Turkish glued case-suffix protection: explicit, measured opt-in (2026-09-30)

The residual `Ateşi` / `Ateşin` gap above is fixed. A generic matcher would
have been unsafe: scanning 48 Turkish subtitles with their active protected
forms found 1,239 name-plus-lowercase-letter matches, dominated by ordinary
or derivational forms such as `Canım` (128), `Efendim` (377), `Ateşli`, and
`Sevilmez`. The fix therefore does not widen `_bounded()` globally.

`Entity.turkish_case_suffixes` is a per-entity opt-in, loaded from glossary
YAML into `GlossaryMap`. For an opted-in name, `protect()` and `restore()`
also recognize direct, vowel-harmonized accusative, dative, genitive,
locative, ablative, and instrumental endings, including the appropriate
buffer consonant and locative voicing. The match requires the complete case
ending to end at a Turkish lowercase boundary, so it cannot consume the
start of a longer derivation or affectionate form. `tvdb-435293.yaml` opts
in only `Ateş`; generic names such as `Can` remain on the prior exact-match
behavior.

Measured against all 49 locally available Turkish subtitles, the opt-in
changed exactly 14 source occurrences in four "If You Love" episodes:
`Ateşi` (5), `Ateşin` (3), `Ateşle` (3), `Ateşten` (2), and `Ateşe` (1).
No unrelated source lines changed. Regressions cover the two reported
sentences, the measured direct forms, non-matches for `Ateşli`, `Ateşler`,
and `Ateşciğim`, and the non-opted ordinary-word collision `Cani`. Full
backend suite: 1215 passed, 48 subtests.
