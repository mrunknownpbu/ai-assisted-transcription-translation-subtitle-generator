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
from asr import AsrConfig, PIPELINE_VERSION, transcribe as asr_transcribe
from output import write_srt_atomic, resolve_output_path
from projection import ProjectedCue, merge_groups, project, validate_coverage
from qc import entity_qc, output_qc, readability_qc, timing_qc, transcription_qc, translation_qc
from qc.types import JobQc
from transcript import (CanonicalTranscript, ModelInfo, auto_lookup_key, cache_key,
                        join_words, validate_cached_transcript)


AUTO = "auto"
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.5
# "continue": proceed with the best detection, just flag it explicitly.
# "require_override": fail the job so a human picks the language manually.
DEFAULT_LOW_CONFIDENCE_ACTION = "continue"


class LowConfidenceLanguageError(RuntimeError):
    """source_lang=AUTO and the detected language's confidence is below
    low_confidence_threshold, with low_confidence_action="require_override"."""


class UnsupportedLanguageError(RuntimeError):
    """The resolved source language (detected or manually requested) has
    no configured NLLB translation mapping (see translate.NLLB_LANG)."""


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
    target_language: str = "en"
    requested_audio_stream: int | None = None
    selected_audio_stream: int = 0
    embedded_stream_language: str | None = None
    stream_selection_mode: str = "AUTO"
    selected_stream_reason: str = ""


def _emit(on_event, events, name: str, **data):
    events.append((name, data))
    if on_event:
        on_event(name, data)


def run(video_path: str, media_root: str, work_dir: str, *,
       source_lang: str = AUTO, audio_stream_index: int | None = None,
       glossary_entities: list[glossary_mod.Entity] | None = None,
       asr_config: AsrConfig | None = None, translation_config=None,
       whisper_model=None, translation_model=None, translation_tok=None, translation_bos=None,
       stream_sampler=None, transcript_cache_dir: str | None = None,
       write_output: bool = True, allow_overwrite: bool = False,
       low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
       low_confidence_action: str = DEFAULT_LOW_CONFIDENCE_ACTION,
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
    post-load safety check) -- not yet wired into worker.py/api.py for
    real jobs, dev/test use only for now."""
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
    asr_config = asr_config or AsrConfig(language=asr_language)

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
                     "vad_filter": asr_config.vad_filter, "compute_type": asr_config.compute_type}
        if source_lang == AUTO:
            lookup_key = auto_lookup_key(media_hash, audio_stream_index, selected_stream.codec,
                                         asr_config.model_name, asr_params, None, PIPELINE_VERSION)
        else:
            estimated_model = ModelInfo(name=f"faster-whisper-{asr_config.model_name}",
                                        version=asr_config.model_name, parameters=asr_params)
            lookup_key = cache_key(media_hash, audio_stream_index, selected_stream.codec,
                                   source_lang, estimated_model, None, PIPELINE_VERSION)
        cache_path = Path(transcript_cache_dir) / f"{lookup_key}.json"
        if cache_path.exists():
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

    source_cues = segmentation_source.build_cues(live_words, language=transcript.language)
    _emit(on_event, events, "SOURCE_SEGMENTATION_COMPLETED", cues=len(source_cues))

    glossary_map = glossary_mod.build_glossary(glossary_entities) if glossary_entities else {}
    spans = translate.build_context_spans(source_cues)
    sentences = [join_words([source_cues[i].text for i in span], transcript.language) for span in spans]
    protected_sentences = [glossary_mod.protect(s, glossary_map) for s in sentences] if glossary_map else sentences

    target_language = "en"
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
            source_cues, spans, transcript.language, glossary_map=glossary_map,
            config=translation_config, model=translation_model, tok=translation_tok, bos=translation_bos,
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
        for i, (protected_source, candidate) in enumerate(zip(protected_sentences, translations)):
            shortfall = glossary_mod.entity_occurrence_report(protected_source, candidate, glossary_map)
            if any(src > tgt for src, tgt in shortfall.values()):
                translations[i] = glossary_mod.recover_dropped_entities(
                    protected_source, candidate, glossary_map)
                entity_recovered += 1
    if entity_recovered:
        _emit(on_event, events, "ENTITY_RECOVERY_APPLIED", sentences=entity_recovered)

    groups = merge_groups(source_cues)
    span_of_group: list[int] = []
    for gi, group in enumerate(groups):
        owning_span = next((si for si, span in enumerate(spans) if group.indices[0] in span), 0)
        span_of_group.append(owning_span)

    target_cues_by_group = []
    for gi, group in enumerate(groups):
        si = span_of_group[gi]
        span = spans[si]
        span_translation = translations[si]
        span_groups_here = [g for g, s in enumerate(span_of_group) if s == si]
        weights = [sum(max(len(source_cues[i].text.split()), 1) for i in groups[g].indices)
                  for g in span_groups_here]
        words = span_translation.split()
        my_pos = span_groups_here.index(gi)
        total_w = sum(weights) or 1
        start_frac = sum(weights[:my_pos]) / total_w
        end_frac = sum(weights[:my_pos + 1]) / total_w
        my_words = words[int(round(start_frac * len(words))):int(round(end_frac * len(words)))]
        my_text = " ".join(my_words) if my_words else span_translation
        target_cues_by_group.append(segmentation_target.segment(my_text, group.start, group.end))
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
