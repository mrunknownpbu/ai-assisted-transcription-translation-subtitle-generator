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

import threading
import traceback
from pathlib import Path

import api
import gpu
import pipeline
from jobstore import JobStore
from output import OutputSafetyError, resolve_output_path, write_srt_atomic


class JobCancelled(RuntimeError):
    pass


class Worker(threading.Thread):
    def __init__(self, store: JobStore, media_root: str, work_root: str,
                 glossary_entities=None, poll_interval: float = 1.0,
                 transcript_cache_dir: str | None = None):
        super().__init__(name="subtitle-ai-worker", daemon=True)
        self.store = store
        self.media_root = media_root
        self.work_root = Path(work_root)
        self.glossary_entities = glossary_entities or []
        self.poll_interval = poll_interval
        self.transcript_cache_dir = transcript_cache_dir
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            job = self.store.claim()
            if not job:
                self._stop_event.wait(self.poll_interval)
                continue
            self._process(job)

    def _process(self, job: dict) -> None:
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

            def on_event(name, data):
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
                    glossary_entities=self.glossary_entities,
                    transcript_cache_dir=cache_dir,
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
                self.store.finish(job_id, "completed", outputs=outputs, qc=result.qc.to_dict())
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

            self.store.finish(job_id, "completed", outputs=outputs, qc=result.qc.to_dict())
        except JobCancelled:
            self.store.finish(job_id, "cancelled")
        except OutputSafetyError as exc:
            self.store.finish(job_id, "failed", error=str(exc), error_category="OUTPUT_ERROR")
        except pipeline.LowConfidenceLanguageError as exc:
            self.store.finish(job_id, "failed", error=str(exc), error_category="LOW_CONFIDENCE_LANGUAGE")
        except pipeline.UnsupportedLanguageError as exc:
            self.store.finish(job_id, "failed", error=str(exc), error_category="UNSUPPORTED_LANGUAGE")
        except Exception as exc:
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
