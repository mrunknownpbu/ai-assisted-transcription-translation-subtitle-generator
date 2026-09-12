"""ASR: audio -> CanonicalTranscript. Never the reverse, and never fed
any existing subtitle file (audio-first guarantee -- see media.py, which
only ever reads video/audio streams).

Configuration is explicit and documented here, not copied blindly from
the prior implementation:

  beam_size=5           Faster-whisper's own recommended default for
                         batch (non-realtime) accuracy over throughput --
                         this pipeline runs as a background job, not
                         live, so there is no reason to trade accuracy
                         for speed.
  temperature=fallback   A ladder [0.0, 0.2, 0.4, 0.6, 0.8, 1.0], not a
    ladder                fixed 0. faster-whisper retries a segment at a
                         higher temperature *itself* when its own
                         compression_ratio/logprob/no_speech thresholds
                         judge the greedy (temperature=0) decode bad --
                         first-line, decoder-native hallucination/
                         repetition mitigation, independent of and prior
                         to hallucination.py's own post-hoc scoring
                         (defense in depth, not a replacement for it).
  condition_on_previous_text=False
                         Retained from the prior implementation because
                         the evidence supports it, not by default: this
                         is the single most-documented cause of Whisper's
                         hallucination-*propagation* failure mode --
                         conditioning each segment's decode on the
                         previous segment's (possibly already bad) text
                         lets one hallucination seed several more. Real
                         motivating pattern from the Season 01 audit:
                         "Altyazı M.K." recurred non-locally across an
                         episode, exactly the shape conditioning-driven
                         propagation takes. Breaking the chain per segment
                         trades a little cross-segment coherence for
                         materially less hallucination spread.
  word_timestamps=True   Required -- see transcript.Word; nothing
                         downstream may fall back to segment-only timing
                         when word timing is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from transcript import CanonicalTranscript, ModelInfo, Segment, Word

PIPELINE_VERSION = "2.0.0"


@dataclass
class AsrConfig:
    model_name: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    beam_size: int = 5
    temperature: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    compression_ratio_threshold: float = 2.4
    logprob_threshold: float = -1.0
    no_speech_threshold: float = 0.6
    condition_on_previous_text: bool = False
    vad_filter: bool = True
    word_timestamps: bool = True
    language: str | None = None      # None = auto-detect


def _model_info(config: AsrConfig, model_version: str) -> ModelInfo:
    return ModelInfo(name=f"faster-whisper-{config.model_name}", version=model_version,
                     parameters={"beam_size": config.beam_size,
                                "temperature": list(config.temperature),
                                "condition_on_previous_text": config.condition_on_previous_text,
                                "vad_filter": config.vad_filter,
                                "compute_type": config.compute_type})


def segments_from_raw(raw_segments: list[dict]) -> list[Segment]:
    """Build Segment/Word objects from faster-whisper's own decoded
    output shape (a list of dicts with word-level timing already
    present) -- separated from transcribe() so this half is unit-testable
    without a GPU or the model loaded."""
    out = []
    for i, seg in enumerate(raw_segments):
        words = [Word(text=w["word"].strip(), original_text=w["word"].strip(),
                      start=w["start"], end=w["end"], probability=w.get("probability"))
                for w in seg.get("words", []) if w.get("word", "").strip()]
        out.append(Segment(index=i, start=seg["start"], end=seg["end"], words=words,
                           avg_logprob=seg.get("avg_logprob", 0.0),
                           no_speech_prob=seg.get("no_speech_prob", 0.0),
                           compression_ratio=seg.get("compression_ratio", 0.0)))
    return out


def transcribe(wav_path: str, media_path: str, media_hash: str, audio_stream_index: int,
               config: AsrConfig | None = None, model=None, on_progress=None, *,
               embedded_stream_language: str | None = None,
               stream_selection_mode: str = "AUTO",
               stream_selection_reason: str = "") -> CanonicalTranscript:
    """`model` is injectable (a pre-loaded faster-whisper.WhisperModel) so
    the caller controls model lifecycle/GPU lock; passing None loads one
    for this call only, mainly for standalone/manual use.

    embedded_stream_language/stream_selection_mode/stream_selection_reason
    are pure provenance passthroughs (see audio_streams.py) -- they never
    influence transcription itself, only what gets recorded about which
    stream was chosen and why."""
    config = config or AsrConfig()
    owns_model = model is None
    from contextlib import nullcontext
    from gpu import gpu_lock
    with gpu_lock() if owns_model else nullcontext():
        try:
            if owns_model:
                # Construction itself belongs inside this try -- a failed
                # WhisperModel(...) call (confirmed: a real CUDA OOM here,
                # under GPU contention) must still reach `finally` below,
                # or whatever CUDA memory ctranslate2 partially allocated
                # before raising is never released for the life of the
                # process (confirmed: VRAM stayed stuck long after the
                # failed job, with no other job running).
                from faster_whisper import WhisperModel
                model = WhisperModel(config.model_name, device=config.device,
                                     compute_type=config.compute_type, download_root="/models")

            segments_iter, info = model.transcribe(
                wav_path, beam_size=config.beam_size, temperature=list(config.temperature),
                compression_ratio_threshold=config.compression_ratio_threshold,
                log_prob_threshold=config.logprob_threshold,
                no_speech_threshold=config.no_speech_threshold,
                condition_on_previous_text=config.condition_on_previous_text,
                vad_filter=config.vad_filter, word_timestamps=config.word_timestamps,
                language=config.language)

            total_duration = getattr(info, "duration", None)
            raw = []
            for seg in segments_iter:
                raw.append({
                    "start": seg.start, "end": seg.end,
                    "avg_logprob": seg.avg_logprob, "no_speech_prob": seg.no_speech_prob,
                    "compression_ratio": seg.compression_ratio,
                    "words": [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                             for w in (seg.words or [])],
                })
                if on_progress and (len(raw) == 1 or len(raw) % 20 == 0):
                    on_progress(seg.end, total_duration, len(raw))
            if on_progress and raw:
                on_progress(raw[-1]["end"], total_duration, len(raw))
            segments = segments_from_raw(raw)
            model_version = getattr(model, "model_size_or_path", config.model_name)
            return CanonicalTranscript(
                media_path=media_path, media_hash=media_hash, audio_stream_index=audio_stream_index,
                language=info.language, language_probability=getattr(info, "language_probability", None),
                asr_model=_model_info(config, str(model_version)), alignment_model=None,
                pipeline_version=PIPELINE_VERSION,
                embedded_stream_language=embedded_stream_language,
                stream_selection_mode=stream_selection_mode,
                stream_selection_reason=stream_selection_reason,
                segments=segments)
        finally:
            if owns_model:
                del model
                from gpu import free_gpu
                free_gpu(config.device)
