"""The worker thread: claims queued jobs and runs pipeline.run() against
them. Model lifecycle (loading/unloading Whisper and NLLB) is owned here,
not by pipeline.py, so a future concurrent-worker design can share or
pool models without pipeline.py changing at all.

Two-step write, matching the "single controlled output layer" mandate:
pipeline.run() writes only to its own scratch `work_dir` (never the media
root). Once the job is validated, THIS module -- and only this module --
copies the validated content into the real, authorized media-root path
via output.write_srt_atomic(), the one place a production write happens.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from pathlib import Path

import api
import auto_glossary
import glossary_profile
import gpu
import pipeline
import srt_translation
from jobstore import JobStore
from output import OutputSafetyError, resolve_media_path, resolve_output_path, write_srt_atomic


logger = logging.getLogger(__name__)


class JobCancelled(RuntimeError):
    pass


class Worker(threading.Thread):
    def __init__(self, store: JobStore, media_root: str, work_root: str,
                 glossary_dir: str | None = None, poll_interval: float = 1.0,
                 transcript_cache_dir: str | None = None,
                 glossary_suggestions_dir: str | None = None,
                 srt_upload_dir: str | None = None,
                 translate_server_url: str | None = None):
        super().__init__(name="subtitle-ai-worker", daemon=True)
        self.store = store
        self.media_root = media_root
        self.work_root = Path(work_root)
        self.glossary_dir = glossary_dir
        self.poll_interval = poll_interval
        self.transcript_cache_dir = transcript_cache_dir
        self.glossary_suggestions_dir = glossary_suggestions_dir
        self.srt_upload_dir = srt_upload_dir
        # None (default) = today's exact behavior: local Tesla P4
        # translation only. Set via TRANSLATE_SERVER_URL (main.py) to
        # route translation to a remote translate-server (see
        # translate.remote_translate_batch()'s docstring for the real
        # benchmark motivating this) with automatic local fallback.
        self.translate_server_url = translate_server_url
        self._stop_event = threading.Event()
        # Real gap this closes (production-readiness audit, 2026-09-21):
        # nothing previously distinguished a wedged-but-alive worker
        # thread from a healthy one -- /api/health only ever reported the
        # API process, not this thread. Updated once per poll iteration
        # in run(), regardless of whether a job was claimed, so a long-
        # running job doesn't itself look like a stall.
        self.last_heartbeat = time.time()

    def _load_glossary_profile(self, video_path: str) -> glossary_profile.Profile:
        """Real defect (2026-09-17): the glossary used to be loaded ONCE
        at process startup with no tvdb_id (see the prior main.py), so
        load_profile() could only ever pick up the global/category layer
        -- a series-specific glossary file, keyed by tvdb_id, would sit in
        the glossary directory and silently never be selected, regardless
        of what it contained. Loading per-job, keyed by THIS job's own
        tvdb_id (extracted from its path -- see glossary_profile.
        find_tvdb_id()), is what actually lets a per-series glossary take
        effect. Never fails a job: a missing directory, a missing/
        malformed series file, or no tvdb_id in the path all degrade to
        an empty Profile, same as before this existed.

        Returns the whole Profile (entities AND phrases -- see
        glossary.PhraseEntry) rather than just entities, so both job
        types can build a phrase_map once the source language is known,
        not just a glossary_map."""
        if not self.glossary_dir:
            return glossary_profile.Profile(tvdb_id=None, title=None)
        try:
            tvdb_id = glossary_profile.find_tvdb_id(video_path)
            return glossary_profile.load_profile(self.glossary_dir, tvdb_id=tvdb_id)
        except (FileNotFoundError, OSError):
            return glossary_profile.Profile(tvdb_id=None, title=None)

    def _refresh_glossary_suggestions(self, video_path: str, glossary_entities: list) -> list:
        """See auto_glossary.py: mines this series' own OTHER already-
        completed sibling episodes for recurring proper names and
        (re)writes /cache/glossary_suggestions/<tvdb_id>.yaml for GUI
        review/one-click promotion (api.py's POST .../glossary/promote).
        Shared by both job types -- an SRT-translation job's own
        source-language sibling (see _process_srt_translation, which
        commits one alongside the video, mirroring what a video job's
        ASR output already does) makes it minable by this exact same
        function with zero changes here.

        Degrades to [] on: no {tvdb-<id>} ancestor (find_series_root
        returns None, same as find_tvdb_id degrading
        _load_glossary_profile), no sibling files, or any read/parse
        error at the series-root level -- per-file errors are already
        handled inside mine_series_entities itself. Never fails a job."""
        try:
            # find_series_root returns an actual filesystem Path used for
            # globbing (unlike find_tvdb_id's plain regex search, which
            # works fine on the bare relative video_path) -- it needs the
            # full path under media_root to resolve to real files on
            # disk, same as resolve_output_path below.
            series_root = glossary_profile.find_series_root(str(Path(self.media_root) / video_path))
            if series_root is None:
                return []
            exclude_path = resolve_output_path(self.media_root, video_path, auto_glossary.SOURCE_LANG)
            known = {form.casefold() for e in glossary_entities for form in e.surface_forms}
            candidates = auto_glossary.mine_series_entities(
                series_root, auto_glossary.SOURCE_LANG,
                exclude_srt_path=exclude_path, exclude_canonicals=known)
            if self.glossary_suggestions_dir:
                tvdb_id = glossary_profile.find_tvdb_id(video_path)
                if tvdb_id is not None:
                    auto_glossary.write_suggestions(self.glossary_suggestions_dir, tvdb_id, candidates)
            return candidates
        except (FileNotFoundError, OSError, OutputSafetyError, ValueError):
            return []

    def _load_auto_hotwords(self, video_path: str, glossary_entities: list) -> list[str]:
        """ASR-hotwords-only view of _refresh_glossary_suggestions() --
        never translation-time protection (that stays exclusively driven
        by glossary_entities, above). Video/ASR path only; SRT-translation
        has no hotwords concept (no audio decoding to bias) and calls
        _refresh_glossary_suggestions() directly instead."""
        candidates = self._refresh_glossary_suggestions(video_path, glossary_entities)
        return [c.canonical for c in candidates]

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info("worker thread started")
        while not self._stop_event.is_set():
            self.last_heartbeat = time.time()
            job = self.store.claim()
            if not job:
                self._stop_event.wait(self.poll_interval)
                continue
            self._process(job)
        logger.info("worker thread stopped")

    def _build_on_event(self, job_id: str):
        """Shared by both job types -- LANGUAGE_DETECTED's data shape
        (language/probability/mode) is identical whether it came from
        pipeline.py's audio-based detection or srt_translation.py's
        text-based one, so no job-type branch is needed for it.
        AUDIO_SELECTED simply never fires for an srt_translation job
        (that pipeline has no audio stream), so its branch is a no-op
        there rather than something that needs excluding."""
        def on_event(name, data):
            # Updated on every pipeline-stage event, not just once per
            # poll loop, so a long-running job's heartbeat stays fresh
            # instead of looking stale for its whole duration.
            self.last_heartbeat = time.time()
            self.store.append_log(job_id, f"{name} {data}")
            if name == "LANGUAGE_DETECTED":
                self.store.update(job_id, detected_language=data["language"],
                                  language_confidence=data["probability"],
                                  source_language_mode=data["mode"])
            elif name == "AUDIO_SELECTED":
                self.store.update(job_id, selected_audio_stream=data["index"],
                                  embedded_stream_language=data.get("embedded_language"),
                                  stream_selection_mode=data.get("selection_mode"),
                                  selected_stream_reason=data.get("reason"))
            if self.store.is_cancel_requested(job_id):
                raise JobCancelled()
        return on_event

    def _process(self, job: dict) -> None:
        if job.get("job_type") == "srt_translation":
            self._process_srt_translation(job)
        else:
            self._process_video(job)

    def _process_srt_translation(self, job: dict) -> None:
        """Original-language .srt -> English .srt: no ASR, no audio, no
        video processing -- see srt_translation.py's module docstring for
        the full list of what this deliberately does NOT reuse from the
        video path below. Follows the identical two-step write pattern
        (scratch dir via the pipeline function, one real atomic write
        here) and the identical exception-cascade-to-terminal-status
        shape as _process_video, so both job types behave predictably the
        same way under failure/cancellation."""
        job_id = job["id"]
        source_lang = job["source_lang"] or "auto"
        try:
            self.store.append_log(job_id, f"Starting SRT translation job for {job['source_srt_path']}")
            destination_path = resolve_media_path(self.media_root, job["destination_srt_path"],
                                                  must_exist=False)
            # An uploaded source lives under srt_upload_dir (app-owned
            # staging, never the media library) -- resolved against that
            # root instead of media_root. The destination above is always
            # under media_root regardless: output goes into the real
            # library no matter where the input came from.
            source_root = self.srt_upload_dir if job.get("source_is_uploaded") else self.media_root
            source_path = resolve_media_path(source_root, job["source_srt_path"], must_exist=True)
            work_dir = self.work_root / job_id
            on_event = self._build_on_event(job_id)

            # Only ever the global/category glossary layer when no episode
            # is associated (job["video_path"] == "") -- find_tvdb_id("")
            # degrades to None exactly like an untagged video path does,
            # never an error. (video_path is required by the API for new
            # jobs today, but this guard is kept for old/legacy rows.)
            if job.get("video_path"):
                glossary_profile_obj = self._load_glossary_profile(job["video_path"])
            else:
                glossary_profile_obj = glossary_profile.Profile(tvdb_id=None, title=None)
            glossary_entities = glossary_profile_obj.entities
            glossary_phrases = glossary_profile_obj.phrases
            # No ASR hotwords here (nothing to bias-decode), but this job's
            # OTHER sibling episodes still refresh the series' mined
            # suggestions -- and, once this job commits its own
            # source-language sibling below, FUTURE jobs mine THIS episode
            # too. Return value unused on purpose.
            if job.get("video_path"):
                self._refresh_glossary_suggestions(job["video_path"], glossary_entities)

            with gpu.gpu_lock():
                api.release_stream_sampler()
                result = srt_translation.run_srt_translation_pipeline(
                    source_srt_path=str(source_path), work_dir=str(work_dir),
                    source_lang=source_lang, target_lang=job.get("target_lang") or "en",
                    glossary_entities=glossary_entities,
                    glossary_phrases=glossary_phrases,
                    translate_remote_url=self.translate_server_url,
                    write_output=True, allow_overwrite=True,  # scratch dir only -- always safe
                    on_event=on_event,
                )

            if not result.valid:
                self.store.finish(job_id, "failed", error="validation failed",
                                  error_category="VALIDATION_ERROR", qc=result.qc.to_dict())
                return

            outputs = []
            # Original-language sibling first, English second -- same
            # ordering _process_video uses, and the same reason: makes an
            # SRT-translation job's on-disk footprint (source-language +
            # English pair beside the video) structurally identical to a
            # video job's, so auto_glossary.mine_series_entities() can
            # mine THIS episode on any FUTURE job for the series, unmodified.
            if job.get("video_path") and result.source_language_srt_path:
                source_language_path = resolve_output_path(
                    self.media_root, job["video_path"], result.detected_source_language)
            else:
                source_language_path = None
            # Same real, reachable edge case _process_video already handles:
            # detected source language equals the target (e.g. an already-
            # English source) -- exactly one output file, not two racing to
            # the same path (see _process_video's identical comment).
            if source_language_path is not None and source_language_path != destination_path:
                content = Path(result.source_language_srt_path).read_text(encoding="utf-8")
                if write_srt_atomic(source_language_path, content,
                                    allow_overwrite=job["overwrite_original"]):
                    outputs.append(str(source_language_path))
                    self.store.append_log(job_id, f"Committed {source_language_path.name}")
                else:
                    self.store.append_log(job_id, f"KEEP: {source_language_path.name} already exists")

            content = Path(result.target_srt_path).read_text(encoding="utf-8")
            if write_srt_atomic(destination_path, content, allow_overwrite=job["overwrite_english"]):
                outputs.append(str(destination_path))
                self.store.append_log(job_id, f"Committed {destination_path.name}")
            else:
                self.store.append_log(job_id, f"KEEP: {destination_path.name} already exists")

            self.store.finish(job_id, "completed", outputs=outputs, qc=result.qc.to_dict(),
                             needs_review=result.qc.needs_review_count())
        except JobCancelled:
            self.store.finish(job_id, "cancelled")
        except OutputSafetyError as exc:
            self.store.finish(job_id, "failed", error=str(exc), error_category="OUTPUT_ERROR")
        except srt_translation.SrtValidationError as exc:
            self.store.finish(job_id, "failed", error=str(exc), error_category="SRT_VALIDATION_ERROR")
        except pipeline.LowConfidenceLanguageError as exc:
            self.store.finish(job_id, "failed", error=str(exc), error_category="LOW_CONFIDENCE_LANGUAGE")
        except pipeline.UnsupportedLanguageError as exc:
            self.store.finish(job_id, "failed", error=str(exc), error_category="UNSUPPORTED_LANGUAGE")
        except Exception as exc:
            logger.exception("job %s failed with an unhandled exception", job_id)
            self.store.append_log(job_id, traceback.format_exc()[-2000:])
            self.store.finish(job_id, "failed", error=str(exc)[:500], error_category="PIPELINE_ERROR")
        finally:
            # See _process_video's identical finally block for why this
            # must be unconditional, not just inside the `with gpu_lock()`.
            gpu.free_gpu()

    def _process_video(self, job: dict) -> None:
        job_id = job["id"]
        source_lang = job["source_lang"] or "auto"
        try:
            self.store.append_log(job_id, f"Starting job for {job['video_path']}")
            # Target is always English today regardless of source-language
            # mode, so it can be validated up front -- fail fast, before
            # spending GPU time, if it's unsafe. The source-language output
            # path depends on the resolved language: for a manual request
            # that's known now; for AUTO it's only known after ASR detects
            # it, so that validation happens after pipeline.run() below.
            target_path = resolve_output_path(self.media_root, job["video_path"], "en")
            source_path = (resolve_output_path(self.media_root, job["video_path"], source_lang)
                          if source_lang != "auto" else None)

            work_dir = self.work_root / job_id
            on_event = self._build_on_event(job_id)

            # A retry must always run ASR fresh, never reuse a cached
            # transcript from the attempt it's retrying (or any earlier
            # one) -- a user hits Retry specifically to get a genuine
            # reprocessing, e.g. hoping a transient bad decode won't
            # repeat, or after supplying a corrected manual language/
            # stream override. ASR is deterministic for identical inputs,
            # so silently serving a cached transcript here would make
            # Retry a no-op for exactly the failure mode it exists to let
            # a human recover from. This is independent of the
            # overwrite_original/overwrite_english KEEP/REPLACE flags,
            # which govern only the final on-disk SRT write, below --
            # neither one substitutes for the other.
            cache_dir = None if job.get("retry_of_job_id") else self.transcript_cache_dir
            glossary_profile_obj = self._load_glossary_profile(job["video_path"])
            glossary_entities = glossary_profile_obj.entities
            glossary_phrases = glossary_profile_obj.phrases
            extra_hotwords = self._load_auto_hotwords(job["video_path"], glossary_entities)

            # Evict the API's cached Analyze-sampler before this job's own
            # GPU-heavy work starts. Confirmed by direct reproduction: the
            # sampler (this deployment's read-only /models only has
            # large-v3 cached, so it is exactly as VRAM-heavy as the real
            # ASR pass -- see api.get_stream_sampler()) plus a freshly-
            # loaded main ASR model leaves a razor-thin, unreliable margin
            # on an 8GB card, and a real production episode OOM'd exactly
            # this way at ASR model construction. A time-based idle
            # eviction or a pre-flight headroom check would only reduce
            # how often a job happens to start soon after an Analyze
            # click -- this unconditional eviction removes that specific
            # collision, at the cost of the next Analyze needing a fresh
            # model load, which is a small and rare price next to a job
            # failing.
            #
            # Both the eviction call AND pipeline.run() itself are wrapped
            # in one outer gpu_lock() -- the mirror-image of the bug above.
            # Without this, pipeline.py's own stages (stream selection,
            # ASR, translation) each acquire and release gpu_lock()
            # separately, freeing their own model before releasing it --
            # leaving gaps BETWEEN stages where a concurrent Analyze click
            # can acquire the lock, load the (cached, not owned-by-it)
            # sampler, and leave it resident in VRAM when it releases,
            # right before this job's next stage tries to construct its
            # own model. Holding one lock for the whole job closes every
            # such gap; gpu_lock()'s reentrancy (see gpu.py) is what lets
            # pipeline.py's own nested acquisitions proceed without
            # deadlocking against this same thread's outer one.
            with gpu.gpu_lock():
                api.release_stream_sampler()

                result = pipeline.run(
                    video_path=str(Path(self.media_root) / job["video_path"]),
                    media_root=self.media_root, work_dir=str(work_dir),
                    source_lang=source_lang, audio_stream_index=job.get("requested_audio_stream"),
                    glossary_entities=glossary_entities,
                    glossary_phrases=glossary_phrases,
                    extra_hotwords=extra_hotwords,
                    transcript_cache_dir=cache_dir,
                    translate_remote_url=self.translate_server_url,
                    write_output=True, allow_overwrite=True,   # scratch dir only -- always safe to overwrite
                    on_event=on_event,
                )

            if not result.valid:
                self.store.finish(job_id, "failed", error="validation failed",
                                  error_category="VALIDATION_ERROR", qc=result.qc.to_dict())
                return

            if source_path is None:
                # AUTO mode: only now, with the real detected language in
                # hand, can the final output path be resolved and safety-
                # validated -- never guessed from source_lang="auto" itself.
                source_path = resolve_output_path(self.media_root, job["video_path"],
                                                  result.detected_source_language)

            outputs = []
            if source_path == target_path:
                # The resolved source language equals the target language
                # (e.g. English audio, English target) -- a real reachable
                # case now that source language is auto-detected. There is
                # exactly ONE meaningful output file here, not two racing
                # to the same path: writing both would silently let
                # whichever ran second clobber the first, and make one of
                # overwrite_original/overwrite_english meaningless. Use
                # the fully-processed target content and the target's own
                # overwrite flag, since that's the pipeline's final output.
                content = Path(result.target_srt_path).read_text(encoding="utf-8")
                if write_srt_atomic(target_path, content, allow_overwrite=job["overwrite_english"]):
                    outputs.append(str(target_path))
                    self.store.append_log(
                        job_id, f"Committed {target_path.name} "
                        "(source language matches target language; single output)")
                else:
                    self.store.append_log(job_id, f"KEEP: {target_path.name} already exists")
                self.store.finish(job_id, "completed", outputs=outputs, qc=result.qc.to_dict(),
                                  needs_review=result.qc.needs_review_count())
                return

            # write_srt_atomic itself is the KEEP/REPLACE decision point:
            # allow_overwrite=False silently declines an existing file
            # (KEEP) rather than raising, so both outputs are always
            # attempted and each one's own overwrite flag governs it.
            content = Path(result.source_srt_path).read_text(encoding="utf-8")
            if write_srt_atomic(source_path, content, allow_overwrite=job["overwrite_original"]):
                outputs.append(str(source_path))
                self.store.append_log(job_id, f"Committed {source_path.name}")
            else:
                self.store.append_log(job_id, f"KEEP: {source_path.name} already exists")

            content = Path(result.target_srt_path).read_text(encoding="utf-8")
            if write_srt_atomic(target_path, content, allow_overwrite=job["overwrite_english"]):
                outputs.append(str(target_path))
                self.store.append_log(job_id, f"Committed {target_path.name}")
            else:
                self.store.append_log(job_id, f"KEEP: {target_path.name} already exists")

            self.store.finish(job_id, "completed", outputs=outputs, qc=result.qc.to_dict(),
                             needs_review=result.qc.needs_review_count())
        except JobCancelled:
            self.store.finish(job_id, "cancelled")
        except OutputSafetyError as exc:
            self.store.finish(job_id, "failed", error=str(exc), error_category="OUTPUT_ERROR")
        except pipeline.LowConfidenceLanguageError as exc:
            self.store.finish(job_id, "failed", error=str(exc), error_category="LOW_CONFIDENCE_LANGUAGE")
        except pipeline.UnsupportedLanguageError as exc:
            self.store.finish(job_id, "failed", error=str(exc), error_category="UNSUPPORTED_LANGUAGE")
        except Exception as exc:
            logger.exception("job %s failed with an unhandled exception", job_id)
            self.store.append_log(job_id, traceback.format_exc()[-2000:])
            self.store.finish(job_id, "failed", error=str(exc)[:500], error_category="PIPELINE_ERROR")
        finally:
            # Confirmed necessary by a real production failure: an OOM
            # raised many frames deep inside a model's own forward pass
            # (e.g. NLLB's self-attention, not just model construction)
            # keeps every one of those intermediate frames -- and whatever
            # GPU tensors their locals reference -- alive via the
            # propagating exception's traceback. asr.py's/translate.py's
            # own `finally: del model; free_gpu()` runs too early to help:
            # it executes WHILE that exception is still unwinding through
            # this call stack, before Python has caught and released it,
            # so gc.collect() can't yet reach those frames and
            # empty_cache() has nothing reclaimable. Only after Python's
            # implicit `del exc` at the end of an `except E as exc:` suite
            # (PEP 3110) actually breaks that reference chain -- which by
            # definition has already happened by the time control reaches
            # this outer finally -- does a second free_gpu() pass have any
            # chance of reclaiming it. Cheap and safe to run unconditionally
            # after every job, success or failure: a no-op when there is
            # nothing left to free.
            gpu.free_gpu()
