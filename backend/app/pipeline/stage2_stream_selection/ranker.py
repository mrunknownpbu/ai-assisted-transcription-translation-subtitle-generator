"""Stage 2: Audio Stream Selection.

Auto-ranks each audio stream found by Stage 1 purely from its own decoded waveform —
frequency content, volume distribution, and voiced-frame patterns — never from stream
titles, language tags, or track ordering (those are unreliable authoring metadata, not
evidence of dialogue). Manual override is a UI/API concern (`selected_by='manual'`),
this module only ever produces the auto ranking.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from app.core.audio import AudioDecodeError, decode_pcm_array
from app.pipeline.interfaces import AudioStreamInfo, StreamRankingScore, StreamSelectionResult

logger = logging.getLogger("subtitle_platform.pipeline.stream_selection")

ANALYSIS_SAMPLE_RATE = 16000
FRAME_MS = 30
MAX_ANALYSIS_SECONDS = 120  # cap decode time on very long files; dialogue-bearing streams
                              # reveal their character well within the first couple of minutes

# Human speech fundamental + formant energy concentrates here.
SPEECH_BAND_HZ = (300.0, 3400.0)


def _decode_mono_pcm(path: Path, stream_index: int, seconds: int = MAX_ANALYSIS_SECONDS) -> np.ndarray:
    return decode_pcm_array(path, stream_index, seconds=seconds, sample_rate=ANALYSIS_SAMPLE_RATE)


def _frame_signal(pcm: np.ndarray, frame_len: int) -> np.ndarray:
    n_frames = len(pcm) // frame_len
    if n_frames == 0:
        return np.empty((0, frame_len), dtype=np.float32)
    return pcm[: n_frames * frame_len].reshape(n_frames, frame_len)


def _speech_band_energy_ratio(frames: np.ndarray, sample_rate: int) -> float:
    if frames.size == 0:
        return 0.0
    window = np.hanning(frames.shape[1])
    spectra = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2
    freqs = np.fft.rfftfreq(frames.shape[1], d=1.0 / sample_rate)
    band_mask = (freqs >= SPEECH_BAND_HZ[0]) & (freqs <= SPEECH_BAND_HZ[1])
    total_energy = spectra.sum() + 1e-12
    band_energy = spectra[:, band_mask].sum()
    return float(band_energy / total_energy)


def _voiced_frame_ratio(frames: np.ndarray) -> float:
    """Cheap energy + zero-crossing-rate voiced/unvoiced heuristic: speech alternates
    between voiced (low ZCR, higher energy) and unvoiced/silent frames, unlike steady
    music beds or constant-tone effects tracks which tend to sit at one ZCR/energy regime."""
    if frames.size == 0:
        return 0.0
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    energy_threshold = np.percentile(rms, 40)
    zcr = np.mean(np.abs(np.diff(np.sign(frames), axis=1)) > 0, axis=1)
    voiced = (rms > energy_threshold) & (zcr < 0.35)
    return float(np.mean(voiced))


def _dynamic_range_db(frames: np.ndarray) -> float:
    if frames.size == 0:
        return 0.0
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    return float(np.percentile(db, 95) - np.percentile(db, 5))


def score_stream(path: Path, stream: AudioStreamInfo) -> StreamRankingScore:
    try:
        pcm = _decode_mono_pcm(path, stream.stream_index)
    except AudioDecodeError as exc:
        logger.warning("Could not decode stream %d for ranking: %s — scoring as 0", stream.stream_index, exc)
        return StreamRankingScore(stream_index=stream.stream_index, dialogue_score=0.0,
                                   breakdown={"error": str(exc)})

    frame_len = int(ANALYSIS_SAMPLE_RATE * FRAME_MS / 1000)
    frames = _frame_signal(pcm, frame_len)

    band_ratio = _speech_band_energy_ratio(frames, ANALYSIS_SAMPLE_RATE)
    voiced_ratio = _voiced_frame_ratio(frames)
    dyn_range = _dynamic_range_db(frames)
    # Dynamic range is normalized against a generous 40dB ceiling — dialogue-centric
    # tracks are rarely more dynamic than that; anything beyond just saturates the score.
    dyn_range_norm = min(dyn_range / 40.0, 1.0)

    dialogue_score = float(0.45 * band_ratio + 0.40 * voiced_ratio + 0.15 * dyn_range_norm)

    return StreamRankingScore(
        stream_index=stream.stream_index,
        dialogue_score=round(dialogue_score, 4),
        breakdown={
            "speech_band_energy_ratio": round(band_ratio, 4),
            "voiced_frame_ratio": round(voiced_ratio, 4),
            "dynamic_range_db": round(dyn_range, 2),
            "dynamic_range_norm": round(dyn_range_norm, 4),
        },
    )


def rank_streams(source_path: str | Path, audio_streams: list[AudioStreamInfo]) -> StreamSelectionResult:
    path = Path(source_path)
    if not audio_streams:
        raise ValueError("No audio streams to rank")

    scores = [score_stream(path, s) for s in audio_streams]
    scores.sort(key=lambda r: r.dialogue_score, reverse=True)

    return StreamSelectionResult(
        rankings=scores,
        selected_stream_index=scores[0].stream_index,
        selected_by="auto",
    )


def apply_manual_override(ranking: StreamSelectionResult, stream_index: int) -> StreamSelectionResult:
    valid_indices = {r.stream_index for r in ranking.rankings}
    if stream_index not in valid_indices:
        raise ValueError(f"stream_index {stream_index} is not among ranked streams {sorted(valid_indices)}")
    return StreamSelectionResult(
        rankings=ranking.rankings,
        selected_stream_index=stream_index,
        selected_by="manual",
    )
