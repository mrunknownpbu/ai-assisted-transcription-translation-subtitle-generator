"""End-to-end orchestration: media -> canonical transcript -> translation
-> target segmentation -> QC -> atomic output.

Each stage emits a structured event via the `on_event` callback (job
system / API wires this to persistent logs; a bare script can pass
`print`). Every write to disk goes through output.py -- this module never
opens a file in the media root directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import audio_streams
import glossary as glossary_mod
import hallucination
import media
import normalize
import segmentation_source
import segmentation_target
import srt
import translate
import turns
from errors import LowConfidenceLanguageError, UnsupportedLanguageError
from asr import AsrConfig, PIPELINE_VERSION, asr_style_prompt, hotwords_enabled, transcribe as asr_transcribe, vad_parameters
from output import TARGET_LANG, write_srt_atomic
from projection import SourceGroup, merge_groups, project, validate_coverage
from qc import entity_qc, output_qc, readability_qc, timing_qc, transcription_qc, translation_qc
from qc.types import JobQc
from transcript import (NO_SPACE_LANGUAGES, CanonicalTranscript, ModelInfo, Segment, auto_lookup_key,
                        cache_key, join_words, validate_cached_transcript)


AUTO = "auto"
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.5
# "continue": proceed with the best detection, just flag it explicitly.
# "require_override": fail the job so a human picks the language manually.
DEFAULT_LOW_CONFIDENCE_ACTION = "continue"


# LowConfidenceLanguageError / UnsupportedLanguageError live in errors.py and
# are imported above, so this module keeps exporting them.


@dataclass
class PipelineResult:
    transcript: CanonicalTranscript
    source_srt_path: Path | None
    target_srt_path: Path | None
    qc: JobQc
    valid: bool
    events: list[tuple[str, dict]] = field(default_factory=list)
    requested_source_language: str = AUTO
    detected_source_language: str = ""
    language_probability: float | None = None
    source_language_mode: str = "AUTO"
    language_detection_uncertain: bool = False
    target_language: str = TARGET_LANG
    requested_audio_stream: int | None = None
    selected_audio_stream: int = 0
    embedded_stream_language: str | None = None
    stream_selection_mode: str = "AUTO"
    selected_stream_reason: str = ""


def _emit(on_event, events, name: str, **data):
    events.append((name, data))
    if on_event:
        on_event(name, data)


def _pack_pieces_by_weight(pieces: list[str], weights: list[int]) -> list[str]:
    """Distribute `pieces` (sentences) across len(weights) buckets,
    proportional to `weights`, preferring to cut exactly BETWEEN two
    sentences (never inside one) -- but every bucket must get at least
    one word (projection.validate_coverage() hard-fails a group with no
    cue at all), so when there are fewer sentences than buckets, the
    buckets that must share a sentence split it at the word level instead
    of each showing the WHOLE shared sentence (real regression this
    fixes, caught on a real S01E01 run, 2026-09-28: several consecutive
    English cues showed byte-identical duplicate text -- an earlier
    version of this function back-filled every empty bucket with the
    full joined text, which is correct in isolation but visibly wrong
    once two adjacent buckets both do it).

    Only in the genuinely pathological case -- fewer WORDS in the whole
    span than buckets -- is there truly not enough distinct content to
    give every group its own slice; there, and only there, every bucket
    gets the same full text (a last resort, not the common path)."""
    text = " ".join(pieces)
    words = text.split()
    n = len(words)
    if n < len(weights):
        return [text] * len(weights)
    sentence_bounds = set()
    cursor = 0
    for p in pieces[:-1]:
        cursor += len(p.split())
        sentence_bounds.add(cursor)
    total_w = sum(weights) or 1
    cuts = [0]
    for bi in range(len(weights) - 1):
        remaining_after = len(weights) - bi - 1
        lo, hi = cuts[-1] + 1, n - remaining_after
        target = min(max(round(sum(weights[:bi + 1]) / total_w * n), lo), hi)
        candidates = [b for b in sentence_bounds if lo <= b <= hi]
        if candidates:
            target = min(candidates, key=lambda b: abs(b - target))
        cuts.append(target)
    cuts.append(n)
    return [" ".join(words[cuts[i]:cuts[i + 1]]) for i in range(len(weights))]


def _merge_groups_into_sentences(pieces: list[str], weights: list[int]) -> list[tuple[int, int, str]]:
    """Fewer English sentences than display groups: give each sentence
    WHOLE to a run of adjacent groups, instead of cutting a sentence
    at the word level so every group gets a slice.

    Real case this replaces (Hammer Session! S01E01, 2026-09-29): one
    span of three groups (1881.2-1895.6s, no real pause between them)
    came back from NLLB as one sentence, "Hey, can you read it?", and was
    shown as three cues "Hey, can" / "you" / "read it?" -- a human editor
    shows it as one cue. The run boundaries are the group boundaries that
    best match where each sentence falls, by cumulative weight vs.
    cumulative characters. All groups here are inside one translation
    span, so merging them never crosses a REAL_ACOUSTIC_GAP or
    UTTERANCE_END boundary (build_context_spans breaks the span there)."""
    k, m = len(weights), len(pieces)
    total_w = sum(weights) or 1
    total_c = sum(len(p) for p in pieces) or 1
    cum_w = [0]
    for w in weights:
        cum_w.append(cum_w[-1] + w)
    bounds = [0]
    for j in range(1, m):
        target = sum(len(p) for p in pieces[:j]) / total_c
        lo, hi = bounds[-1] + 1, k - (m - j)
        bounds.append(min(range(lo, hi + 1), key=lambda b: abs(cum_w[b] / total_w - target)))
    bounds.append(k)
    return [(bounds[j], bounds[j + 1], pieces[j]) for j in range(m)]


def _distribute_span_text(text: str, weights: list[int]) -> list[tuple[int, int, str]]:
    """Split `text` (one translation span) across len(weights) display
    groups, proportional to `weights`. Returns runs `(first, stop, text)`
    over group positions: a run covering more than one group means those
    groups are shown as ONE display window (see run()).

    At LINE boundaries if `text` is multi-speaker dash-formatted
    (glossary.split_multi_speaker_dash_lines, the shape translate_spans()
    itself produces for a dash-formatted source span), else at SENTENCE
    boundaries (segmentation_target.split_sentences) -- never mid-sentence
    when there are fewer sentences than groups: those groups are merged
    instead (_merge_groups_into_sentences). A single-group span (the
    overwhelming common case) always gets `text` back unchanged."""
    k = len(weights)
    if k <= 1:
        return [(0, k, text)]
    dash_lines = glossary_mod.split_multi_speaker_dash_lines(text)
    if dash_lines is not None and len(dash_lines) == k:
        return [(g, g + 1, f"- {line}") for g, line in enumerate(dash_lines)]
    pieces = segmentation_target.split_sentences(text) or [text]
    if len(pieces) < k:
        return _merge_groups_into_sentences(pieces, weights)
    return [(g, g + 1, piece) for g, piece in enumerate(_pack_pieces_by_weight(pieces, weights))]


def _group_weight(cues: list[Segment], language: str) -> int:
    """How much source content a display group holds, for dividing its
    span's translation. Words for spaced languages; characters for an
    unspaced one (transcript.NO_SPACE_LANGUAGES), where `.split()` counts
    every cue as one word -- which is how every Japanese group came to
    weigh the same, whatever its length (2026-09-29, Hammer Session!)."""
    if language in NO_SPACE_LANGUAGES:
        return max(sum(len(c.text.replace(" ", "")) for c in cues), 1)
    return sum(max(len(c.text.split()), 1) for c in cues)


def run(video_path: str, media_root: str, work_dir: str, *,
       source_lang: str = AUTO, audio_stream_index: int | None = None,
       glossary_entities: list[glossary_mod.Entity] | None = None,
       glossary_phrases: list[glossary_mod.PhraseEntry] | None = None,
       asr_config: AsrConfig | None = None, translation_config=None,
       whisper_model=None, translation_model=None, translation_tok=None, translation_bos=None,
       translate_remote_url: str | None = None,
       stream_sampler=None, transcript_cache_dir: str | None = None,
       reuse_cached_transcript: bool = True,
       write_output: bool = True, allow_overwrite: bool = False,
       low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
       low_confidence_action: str = DEFAULT_LOW_CONFIDENCE_ACTION,
       name_correction_context: dict | None = None,
       on_event=None) -> PipelineResult:
    """audio_stream_index=None (the default) means AUTO: enumerate every
    audio stream, exclude obvious non-dialogue tracks, run cheap
    short-sample language ID on what remains, rank, and select the best
    -- see audio_streams.recommend_stream(). A given int is MANUAL: use
    exactly that stream, never rerank or silently switch (see
    worker.py/api.py, which resolve GUI/API stream selection into this
    argument before calling run()).

    transcript_cache_dir=None (the default) means no transcript reuse --
    unchanged, original behavior, and every existing caller/test is
    unaffected. Passing a directory enables an ASR cache keyed on media
    identity + stream + ASR config + pipeline version (see
    transcript.auto_lookup_key() for AUTO mode, transcript.cache_key() for
    MANUAL mode, and transcript.validate_cached_transcript() for the
    post-load safety check). IS wired into main.py/worker.py for real
    jobs (SUBTITLE_AI_TRANSCRIPT_CACHE, passed through Worker's
    constructor) -- worker.py deliberately bypasses it on a retry (see
    Worker._process_video's own comment on why), so this only ever
    serves a first attempt at a job, never masking a genuine re-run."""
    events: list[tuple[str, dict]] = []
    video = Path(video_path)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    requested_source_lang = source_lang
    source_language_mode = "MANUAL" if source_lang != AUTO else "AUTO"
    # AUTO carries no language preference into stream ranking beyond what
    # the ranking's own detection signal already provides.
    preferred_audio_language = None if source_lang == AUTO else source_lang

    requested_audio_stream = audio_stream_index
    stream_selection_mode = "MANUAL" if audio_stream_index is not None else "AUTO"

    _emit(on_event, events, "MEDIA_INSPECTION_STARTED", video=str(video))
    raw_streams, fmt = media.probe(video)
    total_duration = float(fmt.get("duration") or 0) or None
    typed_streams = audio_streams.parse_audio_streams(raw_streams)
    if not typed_streams:
        raise media.MediaError("no audio stream found")

    if audio_stream_index is not None:
        selected_stream = next((s for s in typed_streams if s.index == audio_stream_index), None)
        if selected_stream is None:
            raise media.MediaError(f"requested audio stream index {audio_stream_index} not found")
        selected_stream_reason = "manually selected"
    else:
        recommendation = audio_streams.recommend_stream(
            video, work / "stream_samples", preferred_language=preferred_audio_language,
            sampler=stream_sampler, total_duration=total_duration, streams=typed_streams)
        audio_stream_index = recommendation.recommended_index
        selected_stream = next(s for s in typed_streams if s.index == audio_stream_index)
        selected_stream_reason = recommendation.reason
        _emit(on_event, events, "AUDIO_STREAM_RECOMMENDED", index=audio_stream_index,
             language=recommendation.recommended_language,
             confidence=recommendation.recommended_confidence, reason=selected_stream_reason,
             alternates=[{"index": a.stream.index, "language": a.detected_language,
                         "confidence": a.detection_confidence} for a in recommendation.alternates])

    embedded_stream_language = selected_stream.language
    _emit(on_event, events, "AUDIO_SELECTED", index=audio_stream_index,
         embedded_language=embedded_stream_language, selection_mode=stream_selection_mode,
         reason=selected_stream_reason)

    wav_path = work / f"{video.stem}.wav"
    media_hash = media.content_fingerprint(video)
    # None tells faster-whisper to auto-detect from the audio itself --
    # never from filename, folder name, or any existing subtitle.
    asr_language = None if source_lang == AUTO else source_lang
    # Same per-series entity list translation uses for protection, fed to
    # ASR too -- faster-whisper's hotwords biases decoding toward the
    # correct spelling of known character/place names without forcing
    # them into the output. Built here (flat surface forms, not the
    # placeholder-keyed glossary_map below) because ASR runs before that
    # map exists.
    # Only curated glossary entries (human-reviewed or cast-metadata backed)
    # may bias ASR. Names mined from subtitle files never do: an existing
    # subtitle must not influence Workflow A's reading of the audio (see
    # docs/decisions/2026-10-02-audio-only-asr-inputs.md).
    hotwords = " ".join(sorted(
        {form for e in (glossary_entities or []) for form in e.surface_forms}
    )) or None
    # Off by default: on real E01 data the list induced Title Case output
    # and dropped audio windows (asr.py module docstring has the numbers).
    if not hotwords_enabled():
        hotwords = None
    asr_config = asr_config or AsrConfig(language=asr_language, hotwords=hotwords,
                                        initial_prompt=asr_style_prompt())

    # Cache lookup key is computed BEFORE extraction/ASR so a hit can skip
    # both. AUTO mode uses auto_lookup_key() (no language -- unknown until
    # ASR runs; see transcript.py for why that's safe); MANUAL mode uses
    # cache_key() as originally designed, since the requested language is
    # itself a real ASR input there, known upfront. Either way this is
    # only an ESTIMATE of the eventual asr_model version (the model isn't
    # loaded yet) -- validate_cached_transcript() is the safety net for a
    # hit whose loaded content doesn't actually match.
    cached_transcript = None
    cache_path = None
    if transcript_cache_dir is not None:
        asr_params = {"beam_size": asr_config.beam_size, "temperature": list(asr_config.temperature),
                     "condition_on_previous_text": asr_config.condition_on_previous_text,
                     "vad_filter": asr_config.vad_filter, "vad_parameters": vad_parameters(asr_config),
                     "compute_type": asr_config.compute_type,
                     "hotwords": asr_config.hotwords}
        if source_lang == AUTO:
            lookup_key = auto_lookup_key(media_hash, audio_stream_index, selected_stream.codec,
                                         asr_config.model_name, asr_params, None, PIPELINE_VERSION)
        else:
            estimated_model = ModelInfo(name=f"faster-whisper-{asr_config.model_name}",
                                        version=asr_config.model_name, parameters=asr_params)
            lookup_key = cache_key(media_hash, audio_stream_index, selected_stream.codec,
                                   source_lang, estimated_model, None, PIPELINE_VERSION)
        cache_path = Path(transcript_cache_dir) / f"{lookup_key}.json"
        if reuse_cached_transcript and cache_path.exists():
            try:
                candidate = CanonicalTranscript.load(cache_path)
            except (OSError, ValueError, KeyError):
                candidate = None  # corrupt/unreadable entry -- treat exactly like a miss
            if candidate is not None and validate_cached_transcript(
                    candidate, media_hash=media_hash, audio_stream_index=audio_stream_index,
                    pipeline_version=PIPELINE_VERSION, asr_model_name=asr_config.model_name):
                cached_transcript = candidate
                _emit(on_event, events, "ASR_CACHE_HIT", key=lookup_key)

    if cached_transcript is not None:
        transcript = cached_transcript
    else:
        _emit(on_event, events, "AUDIO_EXTRACTION_STARTED")
        # Explicit stream index, never ffmpeg's implicit default-stream pick --
        # media.extract_audio() has always mapped 0:<index> explicitly.
        media.extract_audio(video, audio_stream_index, wav_path)
        _emit(on_event, events, "AUDIO_EXTRACTION_COMPLETED", wav=str(wav_path))

        _emit(on_event, events, "ASR_STARTED", model=asr_config.model_name,
             requested_language=requested_source_lang)
        t0 = time.time()
        transcript = asr_transcribe(
            str(wav_path), str(video), media_hash, audio_stream_index,
            config=asr_config, model=whisper_model,
            embedded_stream_language=embedded_stream_language,
            stream_selection_mode=stream_selection_mode, stream_selection_reason=selected_stream_reason,
            on_progress=lambda pos, total, n: _emit(on_event, events, "ASR_PROGRESS",
                                                    position=pos, total=total, segments=n))
        _emit(on_event, events, "ASR_COMPLETED", segments=len(transcript.segments), seconds=time.time() - t0)
        if cache_path is not None:
            transcript.save(cache_path)
            _emit(on_event, events, "ASR_CACHE_STORED", key=lookup_key)

    detected_language = transcript.language
    language_probability = transcript.language_probability
    _emit(on_event, events, "LANGUAGE_DETECTED", language=detected_language,
         probability=language_probability, mode=source_language_mode,
         requested=requested_source_lang)

    language_detection_uncertain = False
    if (source_language_mode == "AUTO" and language_probability is not None
            and language_probability < low_confidence_threshold):
        language_detection_uncertain = True
        _emit(on_event, events, "LANGUAGE_DETECTION_UNCERTAIN", language=detected_language,
             probability=language_probability, threshold=low_confidence_threshold,
             action=low_confidence_action)
        if low_confidence_action == "require_override":
            raise LowConfidenceLanguageError(
                f"language detection confidence {language_probability:.2f} for "
                f"{detected_language!r} is below threshold {low_confidence_threshold:.2f}; "
                "manual source-language override required")

    _emit(on_event, events, "HALLUCINATION_CHECK_STARTED")
    hallucination_findings = hallucination.detect(transcript.segments, transcript.language)
    n_suppressed = sum(1 for f in hallucination_findings if f.suppress)
    _emit(on_event, events, "HALLUCINATION_CHECK_COMPLETED", suppressed=n_suppressed)

    live_words = []
    for seg in transcript.segments:
        if seg.suppressed:
            continue
        live_words.extend(normalize.normalize_transcript_words(list(seg.words), transcript.language))

    # Misheard character names ("Aydın" -> "Aydan"), from this episode's
    # cast -- see name_correction.py. `name_correction_context`: the
    # worker's {"names_here", "known", "series_root"}; None = off.
    if name_correction_context and name_correction_context.get("names_here"):
        import name_correction
        series_vocab = name_correction.series_vocabulary(
            name_correction_context.get("series_root"), transcript.language, transcript_cache_dir,
            exclude_media=transcript.media_path)
        vocabulary = name_correction.lowercase_vocabulary(live_words, series_vocab)
        live_words = name_correction.correct_words(live_words, name_correction_context["names_here"],
                                                   name_correction_context["known"], vocabulary)
        fixed = [w for w in live_words if any(c.rule_id == "cast-name-one-edit" for c in w.corrections)]
        if fixed:
            examples = sorted({f"{w.original_text.strip()} -> {w.text.strip()}" for w in fixed})[:10]
            _emit(on_event, events, "NAME_CORRECTIONS_APPLIED", words=len(fixed), examples=examples)

    # Speaker-turn detection (SUBTITLE_AI_TURN_DETECTION, off by default
    # -- see turns.py/CLAUDE.md for the measured reason). Voice mode needs
    # the extracted WAV, which an ASR cache hit above skipped getting;
    # extracted here on demand rather than unconditionally for every job.
    turn_mode = turns.turn_detection_mode()
    turn_word_ids = None
    if turn_mode != "off":
        turn_wav_path = None
        if turn_mode == "voice":
            if not wav_path.is_file():
                media.extract_audio(video, audio_stream_index, wav_path)
            turn_wav_path = str(wav_path)
        turn_indices = turns.detect_turns(live_words, turn_wav_path, mode=turn_mode)
        turn_word_ids = frozenset(id(live_words[i]) for i in turn_indices if 0 <= i < len(live_words))
        _emit(on_event, events, "TURN_DETECTION_COMPLETED", mode=turn_mode, turns=len(turn_indices))

    source_cues = segmentation_source.build_cues(live_words, language=transcript.language,
                                                 turn_word_ids=turn_word_ids)
    _emit(on_event, events, "SOURCE_SEGMENTATION_COMPLETED", cues=len(source_cues))

    glossary_map = glossary_mod.build_glossary(glossary_entities) if glossary_entities else {}
    phrase_map = (glossary_mod.build_phrase_map(glossary_phrases, transcript.language)
                 if glossary_phrases else {})
    spans = translate.build_context_spans(source_cues)
    sentences = [join_words([source_cues[i].text for i in span], transcript.language) for span in spans]
    protected_sentences = [glossary_mod.protect(s, glossary_map) for s in sentences] if glossary_map else sentences

    target_language = TARGET_LANG
    if transcript.language == target_language:
        # The resolved source language already IS the target -- a real
        # reachable case now that source language is auto-detected (e.g.
        # English-language audio with an English target). No NLLB call
        # is needed, and worker.py collapses what would otherwise be two
        # identically-named output files into one write -- see there.
        translations = list(sentences)
        _emit(on_event, events, "TRANSLATION_SKIPPED",
             reason="detected source language matches target language")
    else:
        if transcript.language not in translate.NLLB_LANG:
            raise UnsupportedLanguageError(
                f"no NLLB translation mapping configured for resolved source "
                f"language {transcript.language!r}")
        _emit(on_event, events, "TRANSLATION_STARTED", spans=len(spans))
        translations = translate.translate_spans(
            source_cues, spans, transcript.language, glossary_map=glossary_map, phrase_map=phrase_map,
            config=translation_config, model=translation_model, tok=translation_tok, bos=translation_bos,
            remote_url=translate_remote_url,
            on_progress=lambda done, total: _emit(on_event, events, "TRANSLATION_PROGRESS",
                                                  done=done, total=total))
        _emit(on_event, events, "TRANSLATION_COMPLETED", sentences=len(translations))

    # Deterministic entity recovery, per sentence -- confirmed necessary by
    # a real 5-minute audio test: NLLB legitimately collapsed a doubled
    # vocative ("Eda sakin ol Eda." -> "Eda, take it easy.", one mention
    # lost) even though entity_qc correctly detected the shortfall.
    # Detection alone doesn't fix anything; this closes the loop. Uses
    # ONLY glossary.recover_dropped_entities() -- no model, structurally
    # incapable of inventing new semantic content (see its docstring).
    entity_recovered = 0
    if glossary_map:
        for i, (protected_source, candidate_text) in enumerate(zip(protected_sentences, translations)):
            shortfall = glossary_mod.entity_occurrence_report(protected_source, candidate_text, glossary_map)
            if any(src > tgt for src, tgt in shortfall.values()):
                translations[i] = glossary_mod.recover_dropped_entities(
                    protected_source, candidate_text, glossary_map)
                entity_recovered += 1
    if entity_recovered:
        _emit(on_event, events, "ENTITY_RECOVERY_APPLIED", sentences=entity_recovered)

    span_starts = frozenset(span[0] for span in spans[1:])
    groups = merge_groups(source_cues, force_break_before=span_starts)
    span_of_group: list[int] = []
    for gi, group in enumerate(groups):
        owning_span = next((si for si, span in enumerate(spans) if group.indices[0] in span), 0)
        span_of_group.append(owning_span)

    # Redistribute each translation SPAN's text across the (possibly
    # several) display GROUPS it covers -- computed once per span, not
    # per group (the old version recomputed span_groups_here/weights on
    # every group iteration). See _distribute_span_text()'s docstring:
    # this replaced a raw word-count-FRACTION cut (translate.py:343-356's
    # old logic) that could land mid-sentence and always flattened a
    # span's own dash/newline structure via plain .split().
    display_groups: list[SourceGroup] = []
    target_cues_by_group: list[list] = []
    for si, span in enumerate(spans):
        span_groups_here = [groups[g] for g, s in enumerate(span_of_group) if s == si]
        if not span_groups_here:
            continue
        weights = [_group_weight([source_cues[i] for i in g.indices], transcript.language)
                  for g in span_groups_here]
        for first, stop, piece in _distribute_span_text(translations[si], weights):
            run_groups = span_groups_here[first:stop]
            merged = SourceGroup([i for g in run_groups for i in g.indices],
                                 run_groups[0].start, run_groups[-1].end)
            display_groups.append(merged)
            target_cues_by_group.append(segmentation_target.segment(piece, merged.start, merged.end))
    groups = display_groups
    _emit(on_event, events, "TARGET_SEGMENTATION_COMPLETED",
         cues=sum(len(c) for c in target_cues_by_group))

    projected = project(groups, target_cues_by_group)
    # Coverage is validated against the true, audio-derived timing --
    # before any readability-driven extension -- so a display-duration
    # adjustment can never be mistaken for (or mask) a real coverage gap.
    coverage = validate_coverage(source_cues, groups, projected)
    _emit(on_event, events, "PROJECTION_VALIDATED", valid=(coverage.flagged == 0))

    segmentation_target.extend_short_cues(projected)
    _emit(on_event, events, "READABILITY_TIMING_EXTENDED")

    qc = JobQc()
    qc.transcription = transcription_qc.run(transcript.segments, hallucination_findings)
    qc.translation = translation_qc.run(sentences, translations)
    if glossary_map:
        qc.entity = entity_qc.run(" ".join(protected_sentences), " ".join(translations), glossary_map)
    qc.segmentation = coverage
    qc.timing = timing_qc.run(projected)
    qc.readability = readability_qc.run(projected)
    # Advisory only -- same checks as the target's, run on the SOURCE cues
    # too, so a bad source split is visible without affecting `valid` or
    # needs_review_count() (see qc/types.py's JobQc.source_readability/
    # source_output docstring).
    qc.source_readability = readability_qc.run(source_cues)
    _emit(on_event, events, "QC_COMPLETED",
         flagged={k: v.flagged for k, v in qc.__dict__.items() if v is not None})

    valid = (coverage.flagged == 0 and qc.timing.flagged == 0)

    source_path = target_path = None
    if write_output:
        source_path = work / f"{video.stem}.{transcript.language}.srt"
        write_srt_atomic(source_path, srt.render(source_cues), allow_overwrite=allow_overwrite)
        target_path = work / f"{video.stem}.{target_language}.srt"
        write_srt_atomic(target_path, srt.render(projected), allow_overwrite=allow_overwrite)
        qc.output = output_qc.run(target_path)
        qc.source_output = output_qc.run(source_path)   # advisory -- see above
        valid = valid and qc.output.flagged == 0
        _emit(on_event, events, "OUTPUT_COMMITTED", source=str(source_path), target=str(target_path))

    _emit(on_event, events, "JOB_COMPLETED", valid=valid)
    return PipelineResult(transcript=transcript, source_srt_path=source_path, target_srt_path=target_path,
                          qc=qc, valid=valid, events=events,
                          requested_source_language=requested_source_lang,
                          detected_source_language=detected_language,
                          language_probability=language_probability,
                          source_language_mode=source_language_mode,
                          language_detection_uncertain=language_detection_uncertain,
                          target_language=target_language,
                          requested_audio_stream=requested_audio_stream,
                          selected_audio_stream=audio_stream_index,
                          embedded_stream_language=embedded_stream_language,
                          stream_selection_mode=stream_selection_mode,
                          selected_stream_reason=selected_stream_reason)
