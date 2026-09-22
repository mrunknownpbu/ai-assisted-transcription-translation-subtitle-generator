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
  hotwords=OFF by default (SUBTITLE_AI_ASR_HOTWORDS=on to restore)
                         Measured on Love Is In The Air S01E01 against a
                         human SRT (two 20-minute windows, 2026-09-22):
                         the 60-word glossary+mined list made Whisper
                         emit Title Case for ~65-75% of segments
                         ("Kendine Gelemesin Ya Sinir Olsun"), which
                         NLLB then translates into token-spaced English
                         ("It 's all over ."), and it cost 3-4 points of
                         word recall against no hotwords at all. Even an
                         8-name list made whole 30-second windows vanish
                         in one window. The price of turning it off is
                         ~3-5 points of character-name recall; the
                         translation-time glossary still protects names.
  vad_*                  Permissive VAD (onset 0.3 / offset 0.15, 1s
                         min silence, 500ms pad) instead of
                         faster-whisper's defaults (0.5 / 0.35 / 2s /
                         400ms): +1.3 to +4.1 points of word recall over
                         the default VAD in both windows, measured the
                         same way. Loosening no_speech/logprob thresholds
                         changed nothing -- dropped scenes are not from
                         those.
  word_timestamps=True   Required -- see transcript.Word; nothing
                         downstream may fall back to segment-only timing
                         when word timing is available.
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from media import MediaError
from transcript import CanonicalTranscript, ModelInfo, Segment, Word

PIPELINE_VERSION = "2.0.0"


def hotwords_enabled() -> bool:
    """SUBTITLE_AI_ASR_HOTWORDS=on|1|true|yes re-enables feeding the
    glossary/auto-mined names to Whisper as `hotwords`. Off by default --
    see the module docstring for the measured reason."""
    import os
    return os.environ.get("SUBTITLE_AI_ASR_HOTWORDS", "").strip().lower() in {"on", "1", "true", "yes"}

# Real defect (multi-language validation, 2026-09-14): a file's language
# auto-detection previously trusted a SINGLE short window (faster-whisper's
# own internal peek at the start of the given audio) as representative of
# the WHOLE file's language. A real episode ("If You Love" S01E01) has a
# multi-language cold open -- Spanish at 60s, English at 180s -- before
# settling into Turkish from ~10 minutes on; a single-window detection at
# either of those early points confidently (67-95%) picked the wrong
# language for the entire ~2-hour transcription. Sampling several windows
# spread across the file's duration and taking a majority vote is robust
# to this: in the real case above, 3 of 5 windows correctly voted Turkish.
LANGUAGE_PROBE_WINDOW_DURATION = 20.0
LANGUAGE_PROBE_FRACTIONS = (0.10, 0.30, 0.50, 0.70, 0.90)


@dataclass
class AsrConfig:
    model_name: str = "large-v3"
    device: str = "cuda"
    # int8 rather than float16: this deployment's GPU (Tesla P4, Pascal,
    # compute capability 6.1) lacks efficient float16 tensor throughput,
    # and ctranslate2 refuses to run float16 on it outright ("Requested
    # float16 compute type, but the target device or backend do not
    # support efficient float16 computation") -- confirmed by a real job
    # failure on this hardware. int8 is ctranslate2's standard fallback
    # for pre-Volta GPUs and works everywhere newer GPUs do too.
    compute_type: str = "int8"
    beam_size: int = 5
    temperature: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    compression_ratio_threshold: float = 2.4
    logprob_threshold: float = -1.0
    no_speech_threshold: float = 0.6
    condition_on_previous_text: bool = False
    vad_filter: bool = True
    # faster-whisper VadOptions fields (this version names the Silero
    # threshold onset/offset, not threshold). Only used when vad_filter.
    vad_onset: float = 0.3
    vad_offset: float = 0.15
    vad_min_silence_ms: int = 1000
    vad_speech_pad_ms: int = 500
    word_timestamps: bool = True
    language: str | None = None      # None = auto-detect
    # Space-separated known terms (character names, places) that bias
    # decoding toward their correct spelling without forcing them into the
    # output -- built from the job's glossary (see pipeline.py), the same
    # per-series entity list translation already uses for protection.
    # None/empty is a no-op, identical to this field not existing before.
    hotwords: str | None = None


def vad_parameters(config: "AsrConfig") -> dict | None:
    """The VadOptions dict handed to model.transcribe(), or None when the
    VAD is off. Also part of the transcript cache key (see pipeline.py)
    so a VAD change never serves a transcript made under the old one."""
    if not config.vad_filter:
        return None
    return {"onset": config.vad_onset, "offset": config.vad_offset,
            "min_silence_duration_ms": config.vad_min_silence_ms,
            "speech_pad_ms": config.vad_speech_pad_ms}


def _model_info(config: AsrConfig, model_version: str) -> ModelInfo:
    return ModelInfo(name=f"faster-whisper-{config.model_name}", version=model_version,
                     parameters={"beam_size": config.beam_size,
                                "temperature": list(config.temperature),
                                "condition_on_previous_text": config.condition_on_previous_text,
                                "vad_filter": config.vad_filter,
                                "vad_parameters": vad_parameters(config),
                                "compute_type": config.compute_type,
                                "hotwords": config.hotwords})


def segments_from_raw(raw_segments: list[dict], language: str = "") -> list[Segment]:
    """Build Segment/Word objects from faster-whisper's own decoded
    output shape (a list of dicts with word-level timing already
    present) -- separated from transcribe() so this half is unit-testable
    without a GPU or the model loaded.

    `language` is threaded onto every Segment so Segment.text joins words
    correctly for unspaced languages (see transcript.NO_SPACE_LANGUAGES) --
    it never affects decoding, only how word tokens are rendered back into
    running text."""
    out = []
    for i, seg in enumerate(raw_segments):
        words = [Word(text=w["word"].strip(), original_text=w["word"].strip(),
                      start=w["start"], end=w["end"], probability=w.get("probability"))
                for w in seg.get("words", []) if w.get("word", "").strip()]
        out.append(Segment(index=i, start=seg["start"], end=seg["end"], words=words,
                           avg_logprob=seg.get("avg_logprob", 0.0),
                           no_speech_prob=seg.get("no_speech_prob", 0.0),
                           compression_ratio=seg.get("compression_ratio", 0.0),
                           language=language))
    return out


def _wav_duration_seconds(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as f:
        return f.getnframes() / float(f.getframerate())


def _extract_wav_window(wav_path: str, offset: float, duration: float, out_path: str) -> None:
    command = ["ffmpeg", "-y", "-ss", str(offset), "-t", str(duration), "-i", wav_path,
              "-ac", "1", "-ar", "16000", out_path]
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"ffmpeg language-probe window extraction failed: {exc}") from exc


def _majority_language(votes: list[tuple[str, float]]) -> tuple[str, float] | None:
    """votes: one (language, confidence) pair per window that actually had
    detected speech (callers must exclude silent/no-speech windows -- a
    language guess on silence is noise, not a vote). Ties broken by
    highest mean confidence among the tied languages -- the language a
    plurality of windows agreed on wins outright; a tie is the only case
    confidence needs to arbitrate."""
    if not votes:
        return None
    by_language: dict[str, list[float]] = {}
    for lang, conf in votes:
        by_language.setdefault(lang, []).append(conf)
    winner = max(by_language, key=lambda lang: (len(by_language[lang]), sum(by_language[lang]) / len(by_language[lang])))
    confidences = by_language[winner]
    mean_confidence = sum(confidences) / len(confidences)
    # Weighted by agreement, not just the winner's own mean confidence:
    # real defect (code review, 2026-09-17) -- a slim majority (e.g. 3 of
    # 5 windows) where every agreeing window happened to be individually
    # confident previously reported a high probability with ZERO regard
    # for the other 2 windows disagreeing, so pipeline.py's
    # low-confidence-detection override (DEFAULT_LOW_CONFIDENCE_THRESHOLD)
    # could never trip on a genuinely contested vote -- exactly the
    # ambiguous-multi-language case this whole feature exists to catch.
    # Multiplying by the winner's vote share means a contested majority
    # now reports a lower confidence than a unanimous one, even when the
    # agreeing windows were each individually sure.
    agreement = len(confidences) / len(votes)
    return winner, mean_confidence * agreement


def detect_dominant_language(model, wav_path: str, *,
                             fractions: tuple[float, ...] = LANGUAGE_PROBE_FRACTIONS,
                             window_duration: float = LANGUAGE_PROBE_WINDOW_DURATION
                             ) -> tuple[str, float] | None:
    """Samples several short windows spread across `wav_path`'s full
    duration and returns the majority-voted (language, mean_confidence) --
    see this module's docstring for the real multi-language-cold-open
    defect this guards against. Returns None when no window contains
    detected speech (e.g. a silent/near-empty file, or one too short for
    any window to extract) -- the caller falls back to faster-whisper's
    own single-pass auto-detect in that case, unchanged from before this
    existed.

    Reuses the caller's already-loaded `model` -- no second model load, no
    extra VRAM -- so this is cheap relative to the real transcription: a
    handful of short (window_duration-second), beam_size=1 passes, not
    full-file decodes.

    Never raises -- language detection is an enhancement over
    faster-whisper's own single-pass auto-detect, not a new failure mode a
    job can trip on. Any problem reading `wav_path` at all (missing,
    corrupt, unreadable) degrades to None, same as "no window had
    detectable speech."
    """
    try:
        duration = _wav_duration_seconds(wav_path)
    except (OSError, wave.Error):
        return None
    if duration <= window_duration:
        # Too short for a meaningful multi-window spread (every fraction
        # would clamp to the same offset) -- fall back to the caller's
        # original single-pass behavior rather than run N identical,
        # wasted windows.
        return None
    votes: list[tuple[str, float]] = []
    seen_offsets: set[float] = set()
    with tempfile.TemporaryDirectory() as tmp:
        for i, fraction in enumerate(fractions):
            offset = max(0.0, min(duration - window_duration, fraction * duration))
            if offset in seen_offsets:
                # Real defect (code review, 2026-09-17): for a file whose
                # duration is less than roughly 2x window_duration, several
                # fractions clamp to the SAME offset (e.g. a 40s file with
                # a 20s window: fractions 0.50/0.70/0.90 all clamp to the
                # trailing 20s slice) -- extracting and transcribing it
                # again wastes work and, worse, lets one slice cast
                # multiple "independent" votes, skewing the majority
                # toward whatever language that one slice happens to be.
                continue
            seen_offsets.add(offset)
            clip_path = str(Path(tmp) / f"probe_{i}.wav")
            try:
                _extract_wav_window(wav_path, offset, window_duration, clip_path)
            except MediaError:
                continue  # one bad window must not abort detection for the others
            try:
                segments, info = model.transcribe(clip_path, beam_size=1, vad_filter=True,
                                                  word_timestamps=False)
                segments = list(segments)
                if any(s.text.strip() for s in segments):
                    votes.append((info.language, float(info.language_probability)))
            except Exception:
                # Real defect (code review, 2026-09-17): this module's own
                # docstring promises detection "Never raises", but only
                # MediaError from window extraction was ever caught -- a
                # transcribe() failure on a single probe window (this
                # model has a confirmed, recurring CUDA-OOM-under-
                # contention failure mode elsewhere in this file) would
                # propagate straight out of this function and fail the
                # whole job. A probe window is a best-effort vote, not a
                # required step: any failure here just means one fewer
                # vote, same as a window with no detected speech.
                continue
    return _majority_language(votes)


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
                if config.device == "cuda":
                    # Wait out a transient VRAM shortage (e.g. a Tdarr
                    # transcode burst on this shared Tesla P4) instead of
                    # attempting a load that would very likely OOM -- see
                    # gpu.preflight_vram_check()'s docstring.
                    from gpu import preflight_vram_check
                    preflight_vram_check()
                from faster_whisper import WhisperModel
                model = WhisperModel(config.model_name, device=config.device,
                                     compute_type=config.compute_type, download_root="/models")

            # AUTO mode: vote across several windows spread through the
            # whole file rather than trusting faster-whisper's own single
            # early-window guess (see detect_dominant_language()'s
            # docstring for the real multi-language-cold-open defect this
            # fixes). A resolved language is then passed explicitly into
            # the main transcribe() call below -- no different from a
            # MANUAL request from this point on. detect_dominant_language()
            # returning None (no window had detectable speech) falls back
            # to the original behavior unchanged: language=None, whatever
            # faster-whisper's own single-pass detection decides.
            effective_language = config.language
            language_probability_override = None
            if config.language is None:
                probe_result = detect_dominant_language(model, wav_path)
                if probe_result is not None:
                    effective_language, language_probability_override = probe_result

            segments_iter, info = model.transcribe(
                wav_path, beam_size=config.beam_size, temperature=list(config.temperature),
                compression_ratio_threshold=config.compression_ratio_threshold,
                log_prob_threshold=config.logprob_threshold,
                no_speech_threshold=config.no_speech_threshold,
                condition_on_previous_text=config.condition_on_previous_text,
                vad_filter=config.vad_filter, vad_parameters=vad_parameters(config),
                word_timestamps=config.word_timestamps,
                language=effective_language, hotwords=config.hotwords or None)

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
            segments = segments_from_raw(raw, language=info.language)
            model_version = getattr(model, "model_size_or_path", config.model_name)
            # Once an explicit language is passed to model.transcribe()
            # (either a real MANUAL request, or AUTO resolved via the
            # multi-window vote above), faster-whisper's own
            # info.language_probability just reflects confidence in a
            # language it was TOLD to use -- not informative. The vote's
            # own mean confidence across agreeing windows is the
            # meaningful number to record and feed into the
            # low-confidence-threshold check downstream in pipeline.py.
            reported_probability = (language_probability_override
                                    if language_probability_override is not None
                                    else getattr(info, "language_probability", None))
            return CanonicalTranscript(
                media_path=media_path, media_hash=media_hash, audio_stream_index=audio_stream_index,
                language=info.language, language_probability=reported_probability,
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
