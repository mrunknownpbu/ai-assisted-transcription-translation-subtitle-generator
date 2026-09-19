# Turkish (tr) language support

This documents what actually exists for Turkish in this pipeline, what's
deliberately *not* built (and why), and how to extend it safely. It exists
because a request for a "production-grade Turkish language resource" (a
`languages/tr-TR/` tree of ~30 JSON files: morphology, verb conjugation,
slang, profanity, idioms, ASR-error dictionaries, etc.) was scoped down
after inspecting the actual codebase — most of that content would have had
no consumer and would have meant fabricating linguistic data with no real
evidence behind it. This doc is the honest record of what replaced it.

## Architecture: no language-pack system, and why that's fine

Turkish→English translation is done entirely by a pretrained neural model
(NLLB-200-distilled-1.3B, `subtitle_ai/translate.py`) — it does not read a
lexicon, morphology table, or idiom dictionary. The two real levers this
pipeline has for language-specific behavior are:

1. **`subtitle_ai/normalize.py`** — word-level ASR-output correction,
   applied before subtitle segmentation, gated to a specific language.
2. **`subtitle_ai/glossary.py` + `subtitle_ai/glossary_profile.py`** —
   entity name protection (verbatim reinsertion) and forced-translation
   phrase overrides, layered global → language → series, loaded fresh per
   job from YAML files in the glossary directory
   (`/opt/docker/appdata/subtitle-ai/glossary/` in production).

Every real Turkish improvement in this pipeline is one of these two
mechanisms. There is no `languages/tr-TR/` directory, no morphological
analyzer, and none is planned unless the translation architecture itself
changes to need one.

## What's real and active

### 1. Entity protection: fixed a real Turkish-İ bug

`glossary.py`'s `build_glossary()` used to key its internal dict by
`form.casefold()`. Python's `str.casefold()` maps the Turkish capital
dotted `İ` (U+0130) to a **two-codepoint** sequence (`i` + a combining dot
above, U+0307) — a different string than the real `İ` character. Since
`protect()` regex-matched directly against that (mangled) key, any
protected entity containing capital `İ` — `İstanbul`, `İzmir`, `İbrahim`,
`İrem`, `İsmail`, etc. — silently failed to match at all. Confirmed live
before the fix:
```
protect("İstanbul'da yaşıyorum.", glossary)  ->  "İstanbul'da yaşıyorum."   # unprotected
```
Fixed by keying the dict by the **original** surface-form spelling
(deduplicated case-insensitively via a separate tracking set), since
`protect()` already relies on `re.IGNORECASE` for case-insensitive
matching and never needed a pre-casefolded pattern. Apostrophe-suffixed
names (`İstanbul'da`, `Ankara'ya`, `Mehmet'in`) already worked correctly
before and after this fix — `_bounded()`'s ASCII-word-boundary definition
already treats `'` as a boundary. Regression-tested in
`tests/test_glossary.py::TurkishCapitalDottedITests`.

### 2. ASR normalization: real confidence gate, no new speculative rules

`normalize.py`'s `_RULES` table applies word-level corrections when
`language == "tr"`. It previously had one rule (`tr-petunia-petunya`,
correcting Whisper's anglicized "Petunia" to the real "petunya", confirmed
against a human reference transcript) and a hardcoded, unused
`confidence=1.0`.

Added a real threshold: `MIN_NORMALIZATION_CONFIDENCE = 0.85`. A rule
below this confidence leaves the word untouched instead of applying —
"if confidence is insufficient, preserve the original ASR output" is now
an enforced behavior, not just a description.

**Deliberately not added: new colloquial-normalization rules** (`bi`→`bir`,
`napıyorsun`→`ne yapıyorsun`, `geliyom`→`geliyorum`, etc.), even though
these are well-known, real spoken-Turkish forms. Two reasons:
- This module's own design principle (see its file docstring) is strict:
  a rule requires *independent confirmation* the way the petunya case had
  one (a real reference transcript proving Whisper mis-transcribed the
  same audio) — "never guessed, never auto-learned." General linguistic
  knowledge that a colloquial form exists is not that.
- More fundamentally: if a speaker actually says "bi" (the colloquial
  pronunciation), Whisper transcribing it as "bi" is *correct*, not an
  ASR error — "correcting" it to "bir" would be rewriting the speaker's
  actual register, not fixing a transcription mistake. That's a
  linguistic-rewriting operation, which this pipeline's audio-is-truth
  principle and the original request both explicitly warn against.

**How to add one for real**: if a specific colloquial/phonetic form is
observed causing an actual translation defect in real output (the same
evidence bar as the Evren/Yıldız/Fikret glossary fixes), add it to
`_RULES` with a confidence ≥ 0.85 and cite the real evidence in the
`evidence` field, following the existing petunya rule's shape.

### 3. Idiom / expression translation: forced-phrase glossary

Turkish idioms whose literal word-for-word translation is wrong or
unnatural are handled by the same forced-translation phrase mechanism
already built for short-utterance hallucination fixes
(`glossary.PhraseEntry`/`build_phrase_map`, `_turkish.yaml`). This is
the exact "reuse the existing architecture" mechanism, not new code:
```yaml
- source: "Boş ver."
  translation: "Never mind."
- source: "Aynen."
  translation: "Exactly."
```
Currently seeded in `_turkish.yaml`: `Boş ver.`, `Aynen.`, `Fark etmez.`,
`Sorun değil.`, `Önemli değil.`, `Merak etme.`, `Hadi.`, `Bir dakika.` —
plus ~33 earlier entries (common short interjections: `Peki.`, `Tamam.`,
question words, greetings) added when a real "suspiciously long vs.
source length" QC defect was found for several of them (short,
context-free utterances get zero surrounding context in the SRT-
translation pipeline — see `srt_translation.py`'s one-cue-per-span
design — and NLLB sometimes fabricates a continuation). The idiom
entries added alongside them are proactive (not yet each individually
observed as broken in real output) but are the same low-risk category:
an exact whole-segment match, a dominant/unambiguous meaning, easy to
audit and remove if wrong.

**Why not force address terms (`hocam`, `abi`, `kanka`) the same way**:
these are genuinely context-dependent — `hocam` can mean "sir," "boss,"
or "mate" depending on the relationship and tone. Forcing one fixed
translation would often be *wrong*, unlike an idiom with a dominant
meaning. Left to NLLB's own contextual sense; if a specific real
mistranslation is observed, fix it the same evidence-based way as any
other glossary entry, in the series-specific file, not the global
Turkish tier.

## What's deliberately not built, and why

- **`languages/tr-TR/` directory / full morphological analyzer.** No
  consumer exists — NLLB is a fixed pretrained model, not a rule-driven
  translator. Building one "unless the existing architecture requires it"
  (the original request's own qualifier) doesn't apply here.
- **Active filler/discourse-marker removal** (`şey`, `yani`, `işte`,
  fillers vs. meaningful markers). No removal mechanism exists anywhere in
  this pipeline today — only word-level *correction*, never deletion.
  Building one would be new, destructive behavior with no evidence it's
  wanted, and cuts against "audio is the sole source of truth."
- **Automated slang/profanity/address-term translation forcing.** Same
  context-dependency problem as address terms above, at higher stakes
  (getting register/tone wrong is worse than a missed idiom). No
  consumer would safely apply "translate this profanity as X" without
  real context.
- **Question-particle (`mı`/`mi`/`mu`/`mü`) segmentation protection.**
  Investigated `segmentation_source.py`'s cue-boundary logic (triggers:
  acoustic gap, max duration, max length, sentence-end) and
  `segmentation_target.py`'s line-wrapping (which only ever sees
  already-translated **English** text — Turkish particles never reach
  it). Found no confirmed real defect where a particle gets split from
  its host word in a way that harms translation or readability; a
  sentence-ending `?` already keeps a question cue intact in the common
  case. Not fixing an unconfirmed problem.
- **Frontend/config changes.** None needed — `tr` is already in
  `translate.NLLB_LANG`, already served by `GET /api/languages`, and
  already selectable in `TranslateSrtPage.tsx`'s dynamic language
  dropdown.

## Extending this safely

1. **A real mistranslation observed in actual output** (the proven
   pattern: Evren→"Mr. Universe", Yıldız→"Star", Fikret→"Mr. thought") →
   add a protected `Entity` to the relevant series glossary YAML
   (`entities:`, `protected: true`).
2. **A short, context-free utterance mistranslated/hallucinated** (the
   `Peki.`/`Bekle.` pattern) → add a `PhraseEntry` to `_turkish.yaml`
   (`phrases:`, exact-match, whole-segment forced translation).
3. **A genuine ASR mis-transcription of a specific word**, independently
   confirmed against a real reference (the petunya pattern) → add a rule
   to `normalize.py`'s `_RULES` with a real confidence and cited evidence.
4. **Anything context-dependent** (address terms, tone, slang) → do not
   force it. Let NLLB's contextual sense handle it, and only intervene
   with real evidence of a specific wrong output.

Every one of these requires evidence from real job output before being
added — proactive/speculative entries (like the idiom batch in step 2
above) are the exception, not the rule, and are always flagged as such in
their own file comments.
