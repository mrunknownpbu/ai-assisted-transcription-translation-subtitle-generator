"""Text-based language identification -- the text-only counterpart to
asr.py's acoustic language detection, for wherever a job already has
decoded text and needs to know what language a PIECE of it is actually
in (as opposed to what the job's single detected/requested language is
for the whole file).

Two real consumers:
- srt_translation.py: source_lang="auto" for an uploaded whole .srt file
  (one detection over the whole file's text -- unchanged by this module,
  just delegates its core langdetect call here now).
- translate.py: translate_spans()'s per-sentence language-override pass,
  catching a scene that switches language mid-episode (a foreign-language
  cold open, an embedded song) that the job's one detected ASR language
  can't represent on its own -- see CLAUDE.md's dated entry for the real
  motivating case and the measured evidence behind the thresholds below.
"""

from __future__ import annotations

import re

# langdetect's own codes differ from translate.NLLB_LANG's keys for a
# handful of languages -- normalized here, once, rather than wherever
# detection happens to be called.
LANGDETECT_ALIASES = {"iw": "he", "zh-cn": "zh", "zh-tw": "zh"}

# Splits a sentence into finer clauses on sentence-end punctuation OR a
# comma -- detection-signal granularity only, never translation
# granularity (see detect_language_overrides()'s docstring for why).
_CLAUSE_SPLIT = re.compile(r"(?<=[.!?,])\s+")


def _clauses(text: str) -> list[str]:
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(text) if p.strip()]
    return parts or [text]


def detect_text_language(text: str) -> tuple[str, float]:
    """Best-effort language ID for a chunk of already-decoded text.
    Deterministic given a fixed seed (set on every call; cheap and
    idempotent, matching srt_translation.py's prior inline behavior).
    Returns ("und", 0.0) on totally undetectable input (e.g. no live
    text at all) rather than raising."""
    import langdetect
    langdetect.DetectorFactory.seed = 0
    try:
        candidates = langdetect.detect_langs(text) if text.strip() else []
    except langdetect.lang_detect_exception.LangDetectException:
        candidates = []
    if not candidates:
        return "und", 0.0
    top = candidates[0]
    return LANGDETECT_ALIASES.get(top.lang, top.lang), float(top.prob)


# Measured, not guessed (2026-09-29, the "If You Love" S01E01 Spanish
# cold-open investigation -- see CLAUDE.md). langdetect is reliable on
# real full sentences but actively dangerous on short subtitle cues: real
# Turkish "Vay be!" detects as English at p=0.9999, real Turkish "Evet,
# evet." detects as Danish at p=0.9999, and a real Turkish line repeated
# twice ("Berit, Berit nerede?", "Lan sen kime vuruyor musun(lan)?") gets
# the SAME wrong high-confidence answer both times. A naive per-sentence
# confidence-only gate misfires on over 10% of real short Turkish cues
# even at p>=0.90.
#
# Checked across the whole Turkish media library (90,076 real cues, 43
# episodes): the three thresholds below, used TOGETHER as
# detect_language_overrides() does, produced ZERO false positives while
# still catching both real code-switch instances present in that library
# (an embedded English song in one episode, the Spanish cold-open scene in
# another). The run requirement is what actually buys the safety: a
# repeated line produces the SAME wrong answer every time (so it never
# forms a run of 3+ genuinely DIFFERENT qualifying sentences), while a
# real scene keeps producing different sentences that still agree.
MIN_DETECT_CHARS = 20
MIN_DETECT_CONFIDENCE = 0.90
MIN_RUN_LENGTH = 3


def detect_language_overrides(texts: list[str], source_lang: str, *, supported_langs,
                              min_chars: int = MIN_DETECT_CHARS,
                              min_confidence: float = MIN_DETECT_CONFIDENCE,
                              min_run: int = MIN_RUN_LENGTH) -> list[str | None]:
    """One entry per input text, same order and length as `texts` --
    which must already be in original transcript/time order, since the
    run requirement below is only meaningful against real sequential
    dialogue. None means "no override, use source_lang"; a 2-letter code
    in `supported_langs` means "this sentence is confidently NOT
    source_lang and should be translated as that language instead".

    A single sentence, however confident, is never enough on its own --
    see the module docstring's measured false-positive evidence. Only a
    RUN of >=min_run same-language, high-confidence CLAUSES is treated as
    a real scene change rather than noise. Detection runs on `texts`
    split further on commas (see _clauses()) -- signal granularity only,
    never translation granularity, so a whole flat_sentence is still
    translated as one unit even when only PART of it independently
    confirmed the override language. This exists for a real measured gap
    (2026-09-29, "If You Love" S01E01's cold-open scene, second
    production verification pass): ASR sometimes punctuates a run of
    foreign-language dialogue as fewer, comma-joined sentences rather
    than several period-separated ones, and sentence-level detection
    alone then never accumulates enough independent signal to clear
    min_run. Clause-level detection recovers it (3 comma-split fragments
    of what were only 2 sentences) with NO new false positives: the same
    full-library scan that validated the sentence-level thresholds found
    zero additional qualifying runs among genuinely Turkish clauses --
    most of the real Turkish false-positive shapes (short dash-dialogue,
    repeated proper nouns) split into fragments too short to individually
    qualify, rather than producing new spurious agreement. A flat_sentence
    is overridden if ANY of its own clauses lands inside an accepted run
    -- a sentence with one genuinely foreign clause next to one genuinely
    source-language clause (never observed in the real library scan) is
    a disclosed edge case, not a validated one.

    This also naturally excludes an exact (or near-exact) line repeated
    once or twice -- one of the two real false-positive shapes found in
    the library scan -- since langdetect's mistake repeats identically
    but a genuine scene keeps producing different clauses that still
    agree.

    A clause shorter than min_chars neither qualifies as a run member
    NOR breaks a run already in progress (a short exclamation inside a
    real foreign-language scene shouldn't reset the count), but the
    sentence it belongs to also never receives an override on that
    clause alone."""
    n = len(texts)
    overrides: list[str | None] = [None] * n
    run_lang: str | None = None
    run_sentence_indices: list[int] = []

    def flush():
        if run_lang is not None and len(run_sentence_indices) >= min_run:
            for idx in run_sentence_indices:
                overrides[idx] = run_lang

    for sent_idx, text in enumerate(texts):
        for clause in _clauses(text):
            lang, prob = detect_text_language(clause)
            qualifies = (len(clause) >= min_chars and prob >= min_confidence
                        and lang != source_lang and lang in supported_langs)
            if qualifies and lang == run_lang:
                run_sentence_indices.append(sent_idx)
            elif qualifies:
                flush()
                run_lang, run_sentence_indices = lang, [sent_idx]
            elif len(clause) < min_chars:
                continue  # too short to judge -- doesn't break an in-progress run
            else:
                flush()
                run_lang, run_sentence_indices = None, []
    flush()
    return overrides
