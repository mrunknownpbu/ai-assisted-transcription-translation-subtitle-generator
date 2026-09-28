"""Speaker-turn detection: two interchangeable detectors behind one
interface, COMPARED empirically (see benchmark-results/turn-detection-
comparison-*.json) rather than picked by intuition -- natural-dialogue
plan step 3. A "turn" is a word index where a NEW speaker's utterance
begins; downstream (segmentation_source.py), that index becomes a
transcript.BoundaryReason.UTTERANCE_END boundary, which
translate.build_context_spans() already treats as a real break -- each
speaker's sentence gets translated in its own context, with zero changes
needed there.

Both detectors only ever propose a turn AT a sentence boundary (the end
of a complete sentence) -- neither claims a turn mid-sentence, since a
single speaker's own utterance is never itself evidence of a change.

    heuristic_turns(words)                 -- pause / Q&A / short-reply
    voice_turns(words, wav_path)           -- WeSpeaker ONNX embeddings
    detect_turns(words, wav_path, mode)    -- dispatch by
                                               SUBTITLE_AI_TURN_DETECTION
"""

from __future__ import annotations

import os
import re
import wave
from pathlib import Path

import numpy as np

from transcript import Word

_SENTENCE_END = re.compile(r"[.!?…]['\"»)\]]*$")
_QUESTION_END = re.compile(r"\?['\"»)\]]*$")


# --- heuristic ------------------------------------------------------------
#
# Tuned on real Love Is In The Air dialogue -- see benchmark-results/
# turn-detection-comparison-2026-09-28.json. The first cut (PAUSE_THRESHOLD
# =0.5s, any question, any reply <=4 words, none requiring a real pause)
# fired on 1606 of 10025 words in S01E01 alone (~1 in 6) -- Turkish
# dialogue is full of short sentences from the SAME speaker in a row, so
# "short reply" and "ends in a question" are common with no speaker
# change at all. Both weaker signals now also require a non-trivial pause
# (MIN_TURN_GAP) -- a same-speaker continuation runs words together with
# near-zero gap; a real turn (even a fast one) has some.
PAUSE_THRESHOLD = 0.6      # seconds -- alone is enough, even at 0 reply
                           # length/no question. Shorter than
                           # segmentation_source's MAX_GAP (0.8s, already
                           # a hard cue break) so this only ever adds
                           # signal in the 0.6-0.8s band a hard break
                           # doesn't already cover.
MIN_TURN_GAP = 0.15        # required alongside the weaker signals below
SHORT_REPLY_WORDS = 2      # a reply this short right after a completed
                           # sentence, WITH a real pause, reads as a
                           # response, not a continuation


def heuristic_turns(words: list[Word]) -> set[int]:
    """A turn starts after a complete sentence when: the pause before the
    next word alone is long enough (PAUSE_THRESHOLD), OR there is at
    least a small pause (MIN_TURN_GAP) AND (the sentence just completed
    was a question, OR what follows is a short reply, SHORT_REPLY_WORDS)."""
    sentence_end_at = [i for i, w in enumerate(words) if _SENTENCE_END.search(w.text)]
    turns: set[int] = set()
    for k, i in enumerate(sentence_end_at):
        if i + 1 >= len(words):
            continue
        gap = words[i + 1].start - words[i].end
        if gap >= PAUSE_THRESHOLD:
            turns.add(i + 1)
            continue
        if gap < MIN_TURN_GAP:
            continue
        prev_is_question = bool(_QUESTION_END.search(words[i].text))
        next_end = sentence_end_at[k + 1] if k + 1 < len(sentence_end_at) else len(words) - 1
        reply_len = next_end - i
        if prev_is_question or reply_len <= SHORT_REPLY_WORDS:
            turns.add(i + 1)
    return turns


# --- voice ------------------------------------------------------------
#
# WeSpeaker ResNet34 speaker-embedding model, in ONNX (no pyannote, no
# Hugging Face token -- see scripts/download_speaker_model.py). Feature
# extraction is a pure-numpy log-mel-filterbank approximation of Kaldi's
# fbank (no torchaudio/scipy/librosa in this environment) -- NOT verified
# bit-exact against a reference implementation; if voice-detector quality
# looks suspiciously low, check this first.

MODEL_FILENAME = "voxceleb_resnet34_LM.onnx"
MIN_CHUNK_SECONDS = 0.6
SIMILARITY_THRESHOLD = 0.6   # cosine similarity below this between
                              # adjacent chunk embeddings marks a turn


def models_dir() -> Path:
    return Path(os.environ.get("SUBTITLE_AI_MODELS_DIR", "/models"))


def model_path() -> Path:
    return models_dir() / MODEL_FILENAME


def load_session():
    import onnxruntime as ort
    path = model_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"speaker embedding model not found at {path} -- run "
            "scripts/download_speaker_model.py first")
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def _povey_window(n: int) -> np.ndarray:
    idx = np.arange(n)
    return (0.5 - 0.5 * np.cos(2 * np.pi * idx / max(n - 1, 1))) ** 0.85


def _mel_filterbank(num_bins: int, fft_size: int, sample_rate: int,
                    low_freq: float = 20.0, high_freq: float | None = None) -> np.ndarray:
    high_freq = high_freq or sample_rate / 2

    def hz_to_mel(f):
        return 1127.0 * np.log(1.0 + f / 700.0)

    def mel_to_hz(m):
        return 700.0 * (np.exp(m / 1127.0) - 1.0)

    mel_points = np.linspace(hz_to_mel(low_freq), hz_to_mel(high_freq), num_bins + 2)
    bin_freqs = np.floor((fft_size + 1) * mel_to_hz(mel_points) / sample_rate).astype(int)
    n_fft_bins = fft_size // 2 + 1
    fbank = np.zeros((num_bins, n_fft_bins))
    for m in range(1, num_bins + 1):
        f_left, f_center, f_right = bin_freqs[m - 1], bin_freqs[m], bin_freqs[m + 1]
        for k in range(max(f_left, 0), min(f_center, n_fft_bins)):
            fbank[m - 1, k] = (k - f_left) / max(f_center - f_left, 1)
        for k in range(max(f_center, 0), min(f_right, n_fft_bins)):
            fbank[m - 1, k] = (f_right - k) / max(f_right - f_center, 1)
    return fbank


def extract_fbank(samples: np.ndarray, sample_rate: int = 16000, num_mel_bins: int = 80,
                  frame_length_ms: float = 25.0, frame_shift_ms: float = 10.0,
                  preemphasis: float = 0.97) -> np.ndarray:
    """(T, num_mel_bins) log mel filterbank features, per-utterance mean
    normalised -- WeSpeaker's expected input shape. Returns an empty
    (0, num_mel_bins) array for audio shorter than one frame."""
    frame_len = int(round(sample_rate * frame_length_ms / 1000))
    frame_shift = int(round(sample_rate * frame_shift_ms / 1000))
    if len(samples) < frame_len:
        return np.zeros((0, num_mel_bins), dtype=np.float32)
    n_frames = 1 + (len(samples) - frame_len) // frame_shift
    idx = np.arange(frame_len)[None, :] + frame_shift * np.arange(n_frames)[:, None]
    frames = samples[idx].astype(np.float64)
    frames = np.concatenate([frames[:, :1], frames[:, 1:] - preemphasis * frames[:, :-1]], axis=1)
    frames *= _povey_window(frame_len)
    fft_size = 1
    while fft_size < frame_len:
        fft_size *= 2
    spectrum = np.fft.rfft(frames, n=fft_size)
    power = spectrum.real ** 2 + spectrum.imag ** 2
    energies = power @ _mel_filterbank(num_mel_bins, fft_size, sample_rate).T
    feats = np.log(np.maximum(energies, 1e-10)).astype(np.float32)
    return feats - feats.mean(axis=0, keepdims=True)


def embed_chunk(session, samples: np.ndarray, sample_rate: int = 16000) -> np.ndarray | None:
    """None if `samples` is too short to produce any usable frames."""
    feats = extract_fbank(samples, sample_rate)
    if feats.shape[0] < 5:
        return None
    out = session.run(["embs"], {"feats": feats[None, :, :].astype(np.float32)})
    return out[0][0]


def _read_wav(path: str) -> tuple[int, np.ndarray]:
    with wave.open(path, "rb") as f:
        sample_rate = f.getframerate()
        raw = f.readframes(f.getnframes())
        width = f.getsampwidth()
    if width != 2:
        raise ValueError(f"unsupported WAV sample width {width} (expected 16-bit PCM)")
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return sample_rate, samples


def _sentence_word_spans(words: list[Word]) -> list[tuple[int, int]]:
    """(start_idx, end_idx_inclusive) for each complete sentence in
    `words` -- the same boundary rule segmentation_source's sentence
    splitting uses, so a "chunk" here means the same thing a source cue's
    sentence does."""
    spans = []
    start = 0
    for i, w in enumerate(words):
        if _SENTENCE_END.search(w.text):
            spans.append((start, i))
            start = i + 1
    if start < len(words):
        spans.append((start, len(words) - 1))
    return spans


def voice_turns(words: list[Word], wav_path: str, *, session=None,
                similarity_threshold: float = SIMILARITY_THRESHOLD) -> set[int]:
    """Embeds each sentence-sized chunk (>=MIN_CHUNK_SECONDS of audio)
    from `wav_path` and marks a turn where cosine similarity to the
    PREVIOUS chunk's embedding drops below `similarity_threshold`.
    `session` is injectable (anything with a `.run(["embs"], {"feats":
    ...})` method, e.g. a fake in tests) -- None loads the real
    downloaded model (see load_session())."""
    if session is None:
        session = load_session()
    spans = _sentence_word_spans(words)
    sample_rate, samples = _read_wav(wav_path)
    prev_emb = None
    turns: set[int] = set()
    for start, end in spans:
        chunk_start, chunk_end = words[start].start, words[end].end
        if chunk_end - chunk_start < MIN_CHUNK_SECONDS:
            continue
        lo, hi = int(chunk_start * sample_rate), int(chunk_end * sample_rate)
        emb = embed_chunk(session, samples[lo:hi], sample_rate)
        if emb is None:
            continue
        if prev_emb is not None:
            denom = float(np.linalg.norm(prev_emb) * np.linalg.norm(emb)) or 1e-9
            similarity = float(np.dot(prev_emb, emb)) / denom
            if similarity < similarity_threshold:
                turns.add(start)
        prev_emb = emb
    return turns


# --- dispatch ------------------------------------------------------------

def turn_detection_mode() -> str:
    """SUBTITLE_AI_TURN_DETECTION=heuristic|voice|off (default: see
    DEFAULT_MODE below, set from the real comparison's measured result)."""
    raw = os.environ.get("SUBTITLE_AI_TURN_DETECTION", "").strip().lower()
    return raw if raw in {"heuristic", "voice", "off"} else DEFAULT_MODE


# Set from the real measured comparison, not a guess -- see
# benchmark-results/turn-detection-comparison-2026-09-28.json and the
# CLAUDE.md entry summarising it. Both detectors substantially over-
# trigger on real S01E01 dialogue (heuristic: ~11% of words even after
# tightening from an initial ~16%; voice: ~16%, and 110s/episode on CPU)
# -- neither is trustworthy enough to turn on by default yet, so "off" it
# is, even though heuristic is clearly the better of the two (faster,
# higher recall against what little ground truth exists). Available via
# SUBTITLE_AI_TURN_DETECTION for anyone who wants to keep tuning it; the
# real blocker is evaluation ground truth, not code -- the human-subtitle
# corpus this project scores against only marks a turn when two speakers
# share ONE "- A / - B" cue (12 such points in all of S01E01), which is a
# tiny, biased sample of the episode's real speaker changes (most show
# up as ordinary consecutive cues, invisible to this measurement) and
# cannot usefully validate precision.
DEFAULT_MODE = "off"


def detect_turns(words: list[Word], wav_path: str | None = None, *, mode: str | None = None) -> set[int]:
    """Dispatch to the configured (or explicitly requested) detector.
    "voice" with no `wav_path` falls back to "heuristic" rather than
    raising -- a turn detector is an enhancement, never a hard
    requirement for a job to complete."""
    mode = mode or turn_detection_mode()
    if mode == "off":
        return set()
    if mode == "voice" and wav_path:
        return voice_turns(words, wav_path)
    return heuristic_turns(words)
