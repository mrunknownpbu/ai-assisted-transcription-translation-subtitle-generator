"""Original-language SRT -> English SRT translation: a workflow separate
from pipeline.py's ASR-based flow. The uploaded/selected .srt is already
the transcription -- treating it as ASR output would misrepresent its
provenance. This module never touches ASR, hallucination detection, audio
extraction, or source segmentation (all specific to turning raw audio into
a transcript, which this workflow never has to do); the input file's own
timestamps are already authoritative and are preserved, not re-derived.

Shares translate.py (NLLB), glossary.py (entity protection/recovery),
segmentation_target.py (re-wrapping translated text into readable cues),
the QC stages that operate on plain text/cues rather than ASR-specific
signals, gpu.py (GPU lock, via translate.translate_spans), output.py (the
atomic write), and jobstore.py's job queue with the video pipeline.
Deliberately does NOT reuse: hallucination.py (no ASR output to have
hallucinated), transcript.py's cache (keyed on audio identity, which this
workflow has none of), audio_streams.py, segmentation_source.py, or
projection.py/merge_groups() (those solve the ASR many-cues-to-one-
translation-unit problem; an SRT cue's own timing is already correct, so
each cue maps 1:1 onto its own translation with no merging needed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import glossary as glossary_mod
import segmentation_target
import srt
import translate
from output import write_srt_atomic
from pipeline import LowConfidenceLanguageError, UnsupportedLanguageError
from qc import entity_qc, output_qc, readability_qc, timing_qc, translation_qc
from qc.types import JobQc

# Same default as pipeline.py's DEFAULT_LOW_CONFIDENCE_THRESHOLD -- kept as
# an independent constant (not an import) since the two thresholds gate
# conceptually different detectors (audio-based vs. text-based) that only
# happen to share a starting value.
LOW_CONFIDENCE_THRESHOLD = 0.5

# langdetect's own code differs from translate.NLLB_LANG's keys for a
# handful of languages -- normalized here, once, rather than wherever
# detection happens to be called.
_LANGDETECT_ALIASES = {"iw": "he", "zh-cn": "zh", "zh-tw": "zh"}


class SrtValidationError(ValueError):
    """The source .srt is malformed in a way this workflow refuses to
    silently tolerate. srt.parse() (used elsewhere for lenient QA/
    reference-comparison reads, where silently skipping a bad block is
    the right behavior) is deliberately left unchanged; a translation JOB
    run against a broken source file should fail fast and visibly instead
    of quietly translating a truncated subset of it."""


@dataclass
class ValidatedCue:
    number: int
    start: float
    end: float
    text: str


_STAMP_LINE = re.compile(
    r"^(\d+):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2}),(\d{3})\s*$")


def _parse_timestamp_line(line: str, cue_number_context: str) -> tuple[float, float]:
    m = _STAMP_LINE.match(line.strip())
    if not m:
        raise SrtValidationError(f"cue {cue_number_context}: malformed timestamp line: {line!r}")
    g = list(map(int, m.groups()))
    start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
    end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
    if end < start:
        raise SrtValidationError(f"cue {cue_number_context}: end timestamp is before start "
                                 f"timestamp ({line.strip()!r})")
    return start, end


def parse_and_validate(path: str | Path) -> list[ValidatedCue]:
    """Stricter than srt.parse(): rejects (raises SrtValidationError)
    rather than silently skipping a non-integer cue number, a malformed
    timestamp line, or end<start reversed timing. Empty-text cues are the
    one deliberate exception: skipped, not fatal -- matching this
    project's general "warn and continue over a benign gap" convention
    (see hallucination.py's suppression handling) rather than failing an
    otherwise-valid file over a single blank cue.

    Handles the same UTF-8 BOM / CRLF normalization as srt.parse() (this
    is intentionally NOT a call to srt.parse() itself, since that
    function's silent-skip-on-malformed-block behavior loses exactly the
    information this function exists to act on -- reject vs. tolerate)."""
    raw = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    cues: list[ValidatedCue] = []
    for block in raw.split("\n\n"):
        lines = block.strip("\n").split("\n")
        if not any(l.strip() for l in lines):
            continue  # a trailing blank block from the file's final newline(s)
        if len(lines) < 2:
            raise SrtValidationError(f"malformed cue block (missing timestamp line): {block!r}")
        try:
            number = int(lines[0].strip())
        except ValueError as exc:
            raise SrtValidationError(f"cue number is not an integer: {lines[0]!r}") from exc
        start, end = _parse_timestamp_line(lines[1], str(number))
        # Text is 0+ lines -- a cue with NO text line at all (number +
        # timestamp only, immediately followed by the next block) and a
        # cue with an explicit blank text line are the same real-world
        # shape and get identical treatment: skipped, not fatal.
        text = "\n".join(lines[2:]).strip()
        if not text:
            continue  # empty cue: skipped, not fatal -- see docstring
        cues.append(ValidatedCue(number=number, start=start, end=end, text=text))
    return cues


def _detect_source_language(cues: list[ValidatedCue]) -> tuple[str, float]:
    """Best-effort text-based detection for source_lang="auto". There is
    no audio here for a real acoustic language-ID pass (see asr.py, which
    detects from decoded audio, never from text) -- langdetect is the
    text-only equivalent, deterministic given a fixed seed (set on every
    call; cheap and idempotent). Returns ("und", 0.0) on totally
    undetectable input (e.g. no live text at all) rather than raising,
    so the caller's existing low-confidence-threshold handling covers
    this case too instead of needing a separate one."""
    import langdetect
    langdetect.DetectorFactory.seed = 0
    sample = " ".join(c.text for c in cues)[:5000]
    try:
        candidates = langdetect.detect_langs(sample) if sample.strip() else []
    except langdetect.lang_detect_exception.LangDetectException:
        candidates = []
    if not candidates:
        return "und", 0.0
    top = candidates[0]
    return _LANGDETECT_ALIASES.get(top.lang, top.lang), float(top.prob)


def _emit(on_event, events: list, name: str, **data) -> None:
    events.append((name, data))
    if on_event:
        on_event(name, data)


@dataclass
class SrtTranslationResult:
    source_srt_path: Path
    target_srt_path: Path | None
    qc: JobQc
    valid: bool
    requested_source_language: str = "auto"
    detected_source_language: str = ""
    language_probability: float | None = None
    target_language: str = "en"
    # A scratch-rendered copy of the ORIGINAL (untranslated) cues -- distinct
    # from source_srt_path above, which is just "where this job's input came
    # from". Committing this alongside the video (see worker.py) makes an
    # SRT-translation job's on-disk footprint match a video job's
    # (source-language + English pair), which is what lets
    # auto_glossary.mine_series_entities() -- built for the video/ASR path --
    # pick up an SRT-translation episode's real dialogue too, unmodified.
    source_language_srt_path: Path | None = None
    events: list = field(default_factory=list)


def run_srt_translation_pipeline(source_srt_path: str | Path, work_dir: str | Path, *,
                                 source_lang: str = "auto", target_lang: str = "en",
                                 glossary_entities: list[glossary_mod.Entity] | None = None,
                                 glossary_phrases: list[glossary_mod.PhraseEntry] | None = None,
                                 translation_config=None, translation_model=None,
                                 translation_tok=None, translation_bos=None,
                                 write_output: bool = True, allow_overwrite: bool = False,
                                 low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
                                 on_event=None) -> SrtTranslationResult:
    """source_lang="auto" (the default) means "detect from the subtitle
    text itself" (see _detect_source_language) -- an explicit 2-3 letter
    code is a manual override, validated against translate.NLLB_LANG
    exactly like the video pipeline's resolved ASR language is.

    Writes only to `work_dir` (never a real media-root/destination path --
    see output.py's module docstring); the caller (worker.py) is
    responsible for reading the validated scratch output back and
    performing the one real, atomic write to the user-chosen destination,
    identically to how pipeline.run()'s video path works."""
    events: list[tuple[str, dict]] = []
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    requested_source_lang = source_lang

    _emit(on_event, events, "SRT_PARSE_STARTED", source=str(source_srt_path))
    cues = parse_and_validate(source_srt_path)
    _emit(on_event, events, "SRT_PARSE_COMPLETED", cues=len(cues))

    if source_lang == "auto":
        detected_language, probability = _detect_source_language(cues)
        _emit(on_event, events, "LANGUAGE_DETECTED", language=detected_language,
             probability=probability, mode="AUTO", requested="auto")
        if probability < low_confidence_threshold:
            raise LowConfidenceLanguageError(
                f"language detection confidence {probability:.2f} for {detected_language!r} "
                f"is below threshold {low_confidence_threshold:.2f}; manual source-language "
                "override required")
    else:
        detected_language, probability = source_lang, None
        _emit(on_event, events, "LANGUAGE_DETECTED", language=detected_language,
             probability=None, mode="MANUAL", requested=source_lang)

    if detected_language != target_lang and detected_language not in translate.NLLB_LANG:
        raise UnsupportedLanguageError(
            f"no NLLB translation mapping configured for resolved source "
            f"language {detected_language!r}")

    glossary_map = glossary_mod.build_glossary(glossary_entities) if glossary_entities else {}
    protected_sentences = ([glossary_mod.protect(c.text, glossary_map) for c in cues]
                          if glossary_map else [c.text for c in cues])
    phrase_map = (glossary_mod.build_phrase_map(glossary_phrases, detected_language)
                 if glossary_phrases else {})

    if detected_language == target_lang:
        # Resolved source language already IS the target -- no NLLB call
        # needed, mirroring pipeline.py's identical real case.
        translations = [c.text for c in cues]
        _emit(on_event, events, "TRANSLATION_SKIPPED",
             reason="detected source language matches target language")
    else:
        _emit(on_event, events, "TRANSLATION_STARTED", cues=len(cues))
        # One cue per span (never merged into a larger context unit, unlike
        # the video pipeline's build_context_spans()) -- an SRT cue's own
        # timing is already authoritative and must map onto exactly one
        # translation, not a merged group's. ValidatedCue's own `.text`
        # attribute is exactly what translate_spans() needs from `cues`;
        # no adapter object required.
        spans = [[i] for i in range(len(cues))]
        translations = translate.translate_spans(
            cues, spans, detected_language, glossary_map=glossary_map, phrase_map=phrase_map,
            config=translation_config, model=translation_model, tok=translation_tok,
            bos=translation_bos,
            on_progress=lambda done, total: _emit(on_event, events, "SRT_TRANSLATION_PROGRESS",
                                                  done=done, total=total))
        _emit(on_event, events, "TRANSLATION_COMPLETED", sentences=len(translations))

    # Identical deterministic entity recovery to pipeline.py's -- same
    # glossary.recover_dropped_entities() call, same src>tgt shortfall
    # condition, no model call (see glossary.py's own docstring for why
    # that matters).
    entity_recovered = 0
    if glossary_map:
        for i, (protected_source, candidate) in enumerate(zip(protected_sentences, translations)):
            shortfall = glossary_mod.entity_occurrence_report(protected_source, candidate, glossary_map)
            if any(src > tgt for src, tgt in shortfall.values()):
                translations[i] = glossary_mod.recover_dropped_entities(
                    protected_source, candidate, glossary_map)
                entity_recovered += 1
    if entity_recovered:
        _emit(on_event, events, "ENTITY_RECOVERY_APPLIED", sentences=entity_recovered)

    # Preserve each cue's OWN original timing envelope -- no group merging,
    # no projection: the source .srt's timing is already correct, so this
    # is a 1:1 re-wrap, not the video pipeline's many-cues-to-one-span
    # reconciliation problem.
    target_cues = []
    for cue, translated in zip(cues, translations):
        target_cues.extend(segmentation_target.segment(translated, cue.start, cue.end))
    _emit(on_event, events, "TARGET_SEGMENTATION_COMPLETED", cues=len(target_cues))

    qc = JobQc()
    sources_text = [c.text for c in cues]
    qc.translation = translation_qc.run(sources_text, translations)
    if glossary_map:
        qc.entity = entity_qc.run(" ".join(protected_sentences), " ".join(translations), glossary_map)
    qc.timing = timing_qc.run(target_cues)
    qc.readability = readability_qc.run(target_cues)
    _emit(on_event, events, "QC_COMPLETED",
         flagged={k: v.flagged for k, v in qc.__dict__.items() if v is not None})

    valid = qc.timing.flagged == 0

    target_path = None
    source_language_path = None
    if write_output:
        stem = Path(source_srt_path).stem
        # Original (untranslated) cues, rendered as-is -- feeds the same
        # sibling-pair convention auto_glossary.mine_series_entities()
        # already scans for the video/ASR path. Scratch write only --
        # worker.py owns the real KEEP/REPLACE decision for the eventual
        # on-disk commit, same two-step pattern as the target file below.
        source_language_path = work / f"{stem}.{detected_language}.srt"
        write_srt_atomic(source_language_path, srt.render(cues), allow_overwrite=allow_overwrite)

        target_path = work / f"{stem}.{target_lang}.srt"
        write_srt_atomic(target_path, srt.render(target_cues), allow_overwrite=allow_overwrite)
        qc.output = output_qc.run(target_path)
        valid = valid and qc.output.flagged == 0
        _emit(on_event, events, "OUTPUT_COMMITTED", target=str(target_path),
             source_language=str(source_language_path))

    _emit(on_event, events, "JOB_COMPLETED", valid=valid)
    return SrtTranslationResult(
        source_srt_path=Path(source_srt_path), target_srt_path=target_path, qc=qc, valid=valid,
        requested_source_language=requested_source_lang, detected_source_language=detected_language,
        language_probability=probability, target_language=target_lang,
        source_language_srt_path=source_language_path, events=events)
