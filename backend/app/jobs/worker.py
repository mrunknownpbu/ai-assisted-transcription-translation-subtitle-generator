"""Runs one job end to end. Two job types, both dispatched from `JobRunner.run`:

- `audio_pipeline` (default): the full 11-stage pipeline — Stage 3 language detection ->
  Stage 4 ASR w/ fallback -> Stage 5 canonical transcript -> Stage 6 hallucination defense
  -> Stage 7 normalization -> per target language: Stage 8 translation -> Stage 9 target
  segmentation -> Stage 10 timing projection -> Stage 11 QC + output formats. (Stages 1/2
  already happened at media-registration/stream-selection time, before a job even exists.)
- `direct_translation`: translates a user-supplied .srt file's cue text directly — no
  audio, no ASR, no hallucination defense, cue timing taken verbatim from the input file.
  Reuses the same Stage 8 translation engines/glossary and Stage 11 QC/formatters as the
  audio pipeline; never claims the input subtitles were verified against audio.

Every stage function this module calls remains independently testable/importable on its
own, per the isolation contract in `pipeline/interfaces.py` — this is the only module that
knows the full stage order for either job type.

GPU acquisition wraps only the GPU-bound stages (ASR, translation); CPU-only stages
(QC, formatting) never touch the semaphore, so they can't starve concurrent jobs.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.core.audio import decode_pcm_array, extract_wav_file
from app.core.gpu import unload_engines
from app.db.models import (
    AudioStream, HardwareProfileRow, Job, JobStageRun, MediaFile, Output, QcReport as QcReportRow,
    Suppression, Transcript, Translation,
)
from app.hardware import HardwareProfile
from app.jobs.queue import is_canceled, release_gpu_slot, try_acquire_gpu_slot
from app.pipeline.direct_translation.chunking import group_cues_into_source_chunks
from app.pipeline.direct_translation.srt_io import detect_source_language, read_srt_file
from app.pipeline.interfaces import SourceChunk, TargetCue
from app.pipeline.stage3_language_detection.detector import apply_manual_override, detect_language
from app.pipeline.stage4_asr.fallback import build_default_engine_chain, transcribe_with_fallback
from app.pipeline.stage5_canonical_transcript.assembler import build_canonical_transcript
from app.pipeline.stage6_hallucination_defense.defense import (
    HallucinationDefenseConfig, apply_hallucination_defense, compute_voice_activity_spans,
)
from app.pipeline.stage7_normalization.normalizer import normalize_transcript
from app.pipeline.stage8_translation.glossary import Entity, build_glossary, protect
from app.pipeline.stage8_translation.translator import build_default_engine_chain as build_translation_chain
from app.pipeline.stage8_translation.translator import translate_chunks
from app.pipeline.stage9_target_segmentation.chunker import group_into_source_chunks
from app.pipeline.stage9_target_segmentation.segmenter import segment_into_cue_drafts
from app.pipeline.stage10_timing_projection.projector import project_timing
from app.pipeline.stage11_qc_output import qc_entity, qc_output, qc_translation
from app.pipeline.stage11_qc_output.formatters.burned_in import burn_in_subtitles
from app.pipeline.stage11_qc_output.formatters.provenance import write_sidecar
from app.pipeline.stage11_qc_output.formatters.srt import render_srt
from app.pipeline.stage11_qc_output.formatters.vtt_webvtt import render_webvtt
from app.pipeline.stage11_qc_output.qc import QcConfig, run_qc

logger = logging.getLogger("subtitle_platform.jobs.worker")


class JobCanceledError(RuntimeError):
    pass


class JobRunner:
    GPU_SLOT_WAIT_TIMEOUT_S = 1800  # 30 min: long enough to queue behind another job's ASR/translation pass,
                                      # short enough that a misconfigured slot table fails loudly instead of hanging.

    def __init__(self, settings: Settings, hardware_profile: HardwareProfile, worker_id: str = "worker-1"):
        self.settings = settings
        self.hardware_profile = hardware_profile
        self.worker_id = worker_id
        self.wants_gpu = hardware_profile.gpu_count > 0

    def _check_canceled(self, session: Session, job_id: str) -> None:
        if is_canceled(session, job_id):
            raise JobCanceledError(job_id)

    def _log_stage(self, session: Session, job_id: str, stage_name: str, status: str,
                    log: str | None = None, provenance: dict | None = None) -> None:
        session.add(JobStageRun(job_id=job_id, stage_name=stage_name, status=status, log=log,
                                 provenance_json=provenance))
        session.commit()

    def _with_gpu(self, session: Session, job_id: str, fn):
        if not self.wants_gpu:
            return fn()

        import time
        acquired = try_acquire_gpu_slot(session, job_id)
        if not acquired:
            # No free GPU slot right now; block this thread until one frees up rather
            # than oversubscribing VRAM — but only up to a bounded timeout, so a stuck or
            # misconfigured slot table fails the job instead of hanging the worker forever.
            deadline = time.monotonic() + self.GPU_SLOT_WAIT_TIMEOUT_S
            while not try_acquire_gpu_slot(session, job_id):
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"Timed out after {self.GPU_SLOT_WAIT_TIMEOUT_S}s waiting for a free GPU slot"
                    )
                time.sleep(0.5)
        try:
            return fn()
        finally:
            release_gpu_slot(session, job_id)

    def _resolve_entities(self, job: Job) -> list[Entity]:
        """Merges manually-supplied glossary entries with optional TVDB-cast-derived ones.
        Entirely optional: a job with neither `glossary_entities` nor `tvdb_id` gets an
        empty list, and every glossary-aware call downstream treats that as a no-op.

        `job.glossary_entities` is a heterogeneous mix by the time it reaches here: an
        entry the API route derived from TVDB or a local glossary-profile YAML carries a
        `surface_forms` key (see `routes/jobs.py::_resolve_glossary_entities`), while an
        entry the caller supplied explicitly carries `aliases` (the `GlossaryEntityIn`
        schema's field name) -- reading only one of the two silently dropped every
        derived entry's aliases (falling back to canonical-name-only matching) until this
        checked both."""
        entities: list[Entity] = []
        for entry in job.glossary_entities or []:
            surface_forms = entry.get("surface_forms") or entry.get("aliases") or []
            entities.append(Entity(canonical=entry["canonical"], surface_forms=surface_forms))

        if job.tvdb_id:
            from app.pipeline.stage8_translation.tvdb_client import configured, glossary_from_characters
            if configured():
                try:
                    entities.extend(glossary_from_characters(job.tvdb_id))
                except Exception as exc:
                    logger.warning("TVDB glossary enrichment failed for job %s (continuing without it): %s",
                                   job.id, exc)
            else:
                logger.info("Job %s specified tvdb_id but no TVDB API key is configured; skipping enrichment",
                            job.id)

        return entities

    def _build_glossary_map(self, job: Job) -> dict[str, tuple[str, str]]:
        return build_glossary(self._resolve_entities(job))

    def _build_asr_hotwords(self, job: Job) -> str | None:
        """A short hint string biasing faster-whisper's decoding toward this job's known
        proper nouns throughout the whole audio -- see FasterWhisperEngine for why
        `hotwords` and not `initial_prompt`. Built from the same resolved glossary as
        translation protection, so a name added to the local profile or TVDB cast list
        helps both stages with zero extra configuration. Includes every surface form
        (not just each entity's canonical name): dialogue for a nicknamed character
        routinely uses only the nickname ("Melo", not "Melek Yücel"), which is exactly
        the spelling that needs to be hinted for it to help."""
        entities = self._resolve_entities(job)
        names = {entity.canonical.strip() for entity in entities if entity.canonical.strip()}
        for entity in entities:
            names.update(form.strip() for form in entity.surface_forms if form.strip())
        return ", ".join(sorted(names)) if names else None

    def _translate_all_languages(
        self, session: Session, job_id: str, source_chunks: list[SourceChunk], source_language: str,
        target_languages: list[str], glossary_map: dict[str, tuple[str, str]], work_dir: Path,
        progress_start: float, progress_span: float,
    ) -> dict[str, list]:
        """Translates every target language under a *single* GPU-slot acquisition, then
        unloads the translation engines before that slot is released — shared by both job
        types so this GPU-hygiene rule only needs to be implemented once. Splitting this
        out from the per-language QC/segmentation/output work (which follows, GPU-free)
        also means holding the GPU slot never blocks another job during pure CPU work.
        """
        from app.jobs.queue import mark_job_progress

        translation_engines = build_translation_chain(self.settings)
        total = len(target_languages)

        def do_all_translations():
            results = {}
            for i, target_language in enumerate(target_languages):
                self._check_canceled(session, job_id)
                progress = progress_start + (i / max(total, 1)) * progress_span
                mark_job_progress(session, job_id, stage=f"translation:{target_language}", progress_pct=progress)
                results[target_language] = translate_chunks(
                    source_chunks, source_language, target_language, translation_engines, glossary_map,
                )
            # Freed here, inside the GPU-held scope (see the identical reasoning on the
            # ASR side above) — every target language shares one model load/unload cycle
            # rather than reloading NLLB per language.
            unload_engines(translation_engines)
            return results

        all_translated = self._with_gpu(session, job_id, do_all_translations)

        for target_language, translated_chunks in all_translated.items():
            translated_path = work_dir / f"translated_{target_language}.json"
            translated_path.write_text(json.dumps([tc.__dict__ for tc in translated_chunks], indent=2))
            session.add(Translation(
                job_id=job_id, target_language=target_language,
                engine=translated_chunks[0].engine if translated_chunks else "none",
                model_version=translated_chunks[0].model_version if translated_chunks else "none",
                segments_json_path=str(translated_path),
            ))
        session.commit()
        return all_translated

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def run(self, session: Session, job_id: str) -> None:
        job = session.get(Job, job_id)
        if job.job_type == "direct_translation":
            self._run_direct_translation(session, job_id)
        else:
            self._run_audio_pipeline(session, job_id)

    # ------------------------------------------------------------------
    # audio_pipeline
    # ------------------------------------------------------------------

    def _run_audio_pipeline(self, session: Session, job_id: str) -> None:
        from app.jobs.queue import mark_job_progress

        job = session.get(Job, job_id)
        media = session.get(MediaFile, job.media_file_id)
        stream = session.get(AudioStream, job.audio_stream_id)
        work_dir = Path(self.settings.work_dir) / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        glossary_map = self._build_glossary_map(job)

        # --- Stage 3: language detection ---
        self._check_canceled(session, job_id)
        mark_job_progress(session, job_id, stage="language_detection", progress_pct=5.0)
        lang_result = detect_language(media.original_path, stream.stream_index, duration_s=stream.duration_s)
        if job.source_language:
            lang_result = apply_manual_override(lang_result, job.source_language)
        self._log_stage(session, job_id, "language_detection", "done",
                         provenance=lang_result.__dict__ if hasattr(lang_result, "__dict__") else None)

        # --- Stage 4: ASR (GPU-bound) ---
        self._check_canceled(session, job_id)
        mark_job_progress(session, job_id, stage="asr", progress_pct=15.0)
        audio_wav = extract_wav_file(media.original_path, stream.stream_index, work_dir / "audio.wav")
        hotwords = self._build_asr_hotwords(job)
        engines = build_default_engine_chain(self.settings, self.hardware_profile, hotwords=hotwords)

        def do_asr():
            result = transcribe_with_fallback(audio_wav, lang_result.effective_language, engines)
            # Freed here, inside the GPU-held scope, so the semaphore slot below is only
            # released once this job's VRAM is actually back -- releasing the slot first
            # would let a waiting job load its own model on top of memory this job hasn't
            # freed yet, defeating the one-job-per-slot guarantee on a tight-VRAM card.
            unload_engines(engines)
            return result

        outcome = self._with_gpu(session, job_id, do_asr)
        self._log_stage(session, job_id, "asr", "done",
                         log=json.dumps([a.__dict__ for a in outcome.attempts]))

        # --- Stage 5: canonical transcript ---
        transcript = build_canonical_transcript(
            outcome.result, audio_stream_id=stream.id, stream_hash=stream.stream_hash,
            language_detection=lang_result, engine_attempts=outcome.attempts,
            hardware_profile=self.hardware_profile,
        )

        # --- Stage 6: hallucination defense ---
        self._check_canceled(session, job_id)
        mark_job_progress(session, job_id, stage="hallucination_defense", progress_pct=45.0)
        pcm = decode_pcm_array(media.original_path, stream.stream_index)
        vad_spans = compute_voice_activity_spans(pcm)
        hd_config = HallucinationDefenseConfig(
            min_word_confidence=self.settings.min_word_confidence,
            repetition_loop_min_repeats=self.settings.repetition_loop_min_repeats,
            repetition_loop_min_phrase_words=self.settings.repetition_loop_min_phrase_words,
            compression_ratio_threshold=self.settings.hallucination_compression_ratio_threshold,
            no_speech_prob_threshold=self.settings.hallucination_no_speech_prob_threshold,
            low_avg_logprob_threshold=self.settings.hallucination_low_avg_logprob_threshold,
            suppression_score_threshold=self.settings.hallucination_suppression_score_threshold,
            signatures_path=self.settings.hallucination_signatures_path,
        )
        decisions = apply_hallucination_defense(transcript, vad_spans, hd_config, lang_result.effective_language)
        for d in decisions:
            session.add(Suppression(job_id=job_id, segment_id=d.segment_id, reason=d.reason, method=d.method,
                                     threshold_json=d.threshold, raw_text=d.segment_id, decision=d.decision))
        session.commit()

        # --- Stage 7: normalization ---
        mark_job_progress(session, job_id, stage="normalization", progress_pct=55.0)
        normalize_transcript(transcript, lang_result.effective_language)

        transcript_path = work_dir / "canonical_transcript.json"
        transcript_path.write_text(json.dumps({
            "segments": [
                {"segment_id": s.segment_id, "start": s.start, "end": s.end, "text": s.text,
                 "normalized_text": s.normalized_text, "suppressed": s.suppressed,
                 "suppression_reason": s.suppression_reason, "hallucination_score": s.hallucination_score,
                 "avg_confidence": s.avg_confidence}
                for s in transcript.segments
            ],
            "provenance": transcript.provenance,
        }, indent=2, default=str))
        session.add(Transcript(job_id=job_id, audio_stream_id=stream.id, engine=outcome.result.engine,
                                model_version=outcome.result.model_version, language=lang_result.effective_language,
                                raw_json_path=str(transcript_path)))
        session.commit()

        source_chunks = group_into_source_chunks(
            transcript, self.settings.chunk_pause_gap_s,
            max_chunk_chars=self.settings.max_chunk_chars, max_chunk_segments=self.settings.max_chunk_segments,
        )

        qc_config = QcConfig(
            max_chars_per_line=self.settings.max_chars_per_line, max_lines_per_cue=self.settings.max_lines_per_cue,
            max_reading_cps=self.settings.max_reading_cps, min_cue_duration_s=self.settings.min_cue_duration_s,
        )

        # Pass A: all translation (GPU-bound), one model load/unload cycle for every
        # target language. Pass B below (QC/segmentation/timing/output) is CPU-only and
        # never holds the GPU slot.
        all_translated = self._translate_all_languages(
            session, job_id, source_chunks, lang_result.effective_language, job.target_languages, glossary_map,
            work_dir, progress_start=60.0, progress_span=30.0,
        )

        for target_language, translated_chunks in all_translated.items():
            self._check_canceled(session, job_id)
            mark_job_progress(session, job_id, stage=f"qc_and_output:{target_language}", progress_pct=92.0)

            translation_qc_report = qc_translation.run(translated_chunks, target_language=target_language)
            session.add(QcReportRow(job_id=job_id, stage=translation_qc_report.stage, target_language=target_language,
                                     passed=translation_qc_report.passed,
                                     reasons_json=[f.__dict__ for f in translation_qc_report.findings]))
            if glossary_map:
                protected_source = protect(" ".join(tc.source_text for tc in translated_chunks), glossary_map)
                translated_joined = " ".join(tc.translated_text for tc in translated_chunks)
                entity_qc_report = qc_entity.run(protected_source, translated_joined, glossary_map,
                                                  target_language=target_language)
                session.add(QcReportRow(job_id=job_id, stage=entity_qc_report.stage, target_language=target_language,
                                         passed=entity_qc_report.passed,
                                         reasons_json=[f.__dict__ for f in entity_qc_report.findings]))

            drafts = segment_into_cue_drafts(translated_chunks, self.settings.max_chars_per_line,
                                              self.settings.max_lines_per_cue,
                                              max_merge_gap_s=self.settings.chunk_pause_gap_s)
            cues = project_timing(drafts, source_chunks, self.settings.min_cue_duration_s)

            qc_report = run_qc(cues, qc_config, target_language=target_language)
            session.add(QcReportRow(job_id=job_id, stage="stage11_qc", target_language=target_language,
                                     passed=qc_report.passed,
                                     reasons_json=[f.__dict__ for f in qc_report.findings]))
            session.commit()

            provenance = {
                **transcript.provenance,
                "target_language": target_language,
                "translation_engine": translated_chunks[0].engine if translated_chunks else "none",
                "glossary_entity_count": len({c for _, c in glossary_map.values()}) if glossary_map else 0,
                "qc_passed": qc_report.passed and translation_qc_report.passed,
            }

            output_dir = Path(self.settings.output_dir) / job_id
            base_name = Path(media.filename).stem
            self._write_outputs(session, job_id, cues, job.output_formats, output_dir, base_name,
                                 target_language, provenance, media.original_path)

        mark_job_progress(session, job_id, stage="done", progress_pct=100.0)

    # ------------------------------------------------------------------
    # direct_translation
    # ------------------------------------------------------------------

    def _run_direct_translation(self, session: Session, job_id: str) -> None:
        from app.jobs.queue import mark_job_progress

        job = session.get(Job, job_id)
        work_dir = Path(self.settings.work_dir) / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        glossary_map = self._build_glossary_map(job)

        self._check_canceled(session, job_id)
        mark_job_progress(session, job_id, stage="language_detection", progress_pct=10.0)
        parsed_cues = read_srt_file(job.input_srt_path)
        source_language = job.source_language or detect_source_language(parsed_cues)
        self._log_stage(session, job_id, "language_detection", "done",
                         provenance={"detected_language": source_language, "method": "langdetect",
                                     "note": "text-based — no audio exists for this job type"})

        # Adjacent input cues within a real pause gap are grouped into one SourceChunk for
        # *translation context only* -- confirmed by a real 5-episode comparison that
        # translating one bare, isolated cue at a time (the previous 1:1 behavior) scored
        # consistently lower chrF/word-F1 than the audio pipeline's pause-gap-grouped
        # chunks on the exact same underlying text, in every episode. Re-cutting the
        # (possibly reflowed) translated text back into cues and timing each one is then
        # Stage 9/10's job below, exactly as the audio pipeline already does -- a solitary
        # cue (no real neighbor within the pause gap) still gets its own chunk and comes
        # back out with its original timing untouched, via the same share=1.0 case.
        source_chunks = group_cues_into_source_chunks(
            parsed_cues, self.settings.chunk_pause_gap_s,
            max_chunk_chars=self.settings.max_chunk_chars, max_chunk_segments=self.settings.max_chunk_segments,
        )

        qc_config = QcConfig(
            max_chars_per_line=self.settings.max_chars_per_line, max_lines_per_cue=self.settings.max_lines_per_cue,
            max_reading_cps=self.settings.max_reading_cps, min_cue_duration_s=self.settings.min_cue_duration_s,
        )

        all_translated = self._translate_all_languages(
            session, job_id, source_chunks, source_language, job.target_languages, glossary_map,
            work_dir, progress_start=15.0, progress_span=75.0,
        )

        for target_language, translated_chunks in all_translated.items():
            self._check_canceled(session, job_id)
            mark_job_progress(session, job_id, stage=f"qc_and_output:{target_language}", progress_pct=92.0)

            translation_qc_report = qc_translation.run(translated_chunks, target_language=target_language)
            session.add(QcReportRow(job_id=job_id, stage=translation_qc_report.stage, target_language=target_language,
                                     passed=translation_qc_report.passed,
                                     reasons_json=[f.__dict__ for f in translation_qc_report.findings]))
            if glossary_map:
                protected_source = protect(" ".join(tc.source_text for tc in translated_chunks), glossary_map)
                translated_joined = " ".join(tc.translated_text for tc in translated_chunks)
                entity_qc_report = qc_entity.run(protected_source, translated_joined, glossary_map,
                                                  target_language=target_language)
                session.add(QcReportRow(job_id=job_id, stage=entity_qc_report.stage, target_language=target_language,
                                         passed=entity_qc_report.passed,
                                         reasons_json=[f.__dict__ for f in entity_qc_report.findings]))

            drafts = segment_into_cue_drafts(translated_chunks, self.settings.max_chars_per_line,
                                              self.settings.max_lines_per_cue,
                                              max_merge_gap_s=self.settings.chunk_pause_gap_s)
            cues = project_timing(drafts, source_chunks, self.settings.min_cue_duration_s)

            qc_report = run_qc(cues, qc_config, target_language=target_language)
            session.add(QcReportRow(job_id=job_id, stage="stage11_qc", target_language=target_language,
                                     passed=qc_report.passed,
                                     reasons_json=[f.__dict__ for f in qc_report.findings]))
            session.commit()

            provenance = {
                "job_type": "direct_translation",
                "provenance_note": "Translated from a user-supplied subtitle file — not verified against audio.",
                "source_language": source_language,
                "target_language": target_language,
                "translation_engine": translated_chunks[0].engine if translated_chunks else "none",
                "glossary_entity_count": len({c for _, c in glossary_map.values()}) if glossary_map else 0,
                "input_filename": job.input_filename,
                "qc_passed": qc_report.passed and translation_qc_report.passed,
            }

            output_dir = Path(self.settings.output_dir) / job_id
            base_name = Path(job.input_filename or "subtitles").stem
            self._write_outputs(session, job_id, cues, job.output_formats, output_dir, base_name,
                                 target_language, provenance, source_video_path=None)

        mark_job_progress(session, job_id, stage="done", progress_pct=100.0)

    # ------------------------------------------------------------------
    # Shared output writing (both job types)
    # ------------------------------------------------------------------

    def _write_outputs(
        self, session: Session, job_id: str, cues: list[TargetCue], output_formats: list[str],
        output_dir: Path, base_name: str, target_language: str, provenance: dict,
        source_video_path: str | None,
    ) -> None:
        for fmt in output_formats:
            if fmt == "srt":
                content = render_srt(cues)
                dest = output_dir / f"{base_name}.{target_language}.srt"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
            elif fmt in ("vtt", "webvtt"):
                content = render_webvtt(cues, provenance=provenance)
                dest = output_dir / f"{base_name}.{target_language}.{fmt}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
            elif fmt == "burned_in":
                if source_video_path is None:
                    logger.warning("burned_in output requested but this job has no source video; skipping")
                    continue
                dest = output_dir / f"{base_name}.{target_language}.burned_in.mp4"
                burn_in_subtitles(source_video_path, cues, dest,
                                   use_nvenc=(self.hardware_profile.vendor.value == "nvidia"))
            else:
                logger.warning("Unknown output format '%s' requested, skipping", fmt)
                continue

            write_sidecar(str(dest) + ".provenance.json", provenance)
            session.add(Output(job_id=job_id, format=fmt, target_language=target_language,
                                file_path=str(dest), provenance_json=provenance))

            if fmt == "srt":
                output_qc_report = qc_output.run(dest, target_language=target_language)
                session.add(QcReportRow(job_id=job_id, stage=output_qc_report.stage, target_language=target_language,
                                         passed=output_qc_report.passed,
                                         reasons_json=[f.__dict__ for f in output_qc_report.findings]))
        session.commit()
