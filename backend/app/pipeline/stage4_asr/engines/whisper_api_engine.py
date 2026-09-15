"""Fallback #3: OpenAI's hosted Whisper transcription API. Useful when local compute is
unavailable or overloaded, at the cost of sending audio off-host and requiring an API
key. Word-level timestamps are requested via `timestamp_granularities=["word"]`, but the
API does not return a per-word confidence score, so confidence is derived from the
segment-level `avg_logprob` and applied uniformly to that segment's words — documented
here and in the provenance record as a known precision limitation of this engine.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from app.pipeline.interfaces import ASRResult, ASRSegment, ASRWord
from app.pipeline.stage4_asr.engines.base import ASREngineError

logger = logging.getLogger("subtitle_platform.pipeline.asr.whisper_api")


class WhisperAPIEngine:
    name = "whisper_api"

    def __init__(self, api_key: str | None, base_url: str = "https://api.openai.com/v1", model: str = "whisper-1"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @property
    def model_version(self) -> str:
        return f"openai-api:{self.model}"

    def is_available(self) -> tuple[bool, str | None]:
        if not self.api_key:
            return False, "no API key configured (SUBTITLE_OPENAI_WHISPER_API_KEY unset)"
        try:
            import openai  # noqa: F401
        except ImportError as exc:
            return False, f"openai package not importable: {exc}"
        return True, None

    def transcribe(self, audio_path: Path, language: str | None) -> ASRResult:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            with open(audio_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model=self.model, file=f, language=language,
                    response_format="verbose_json", timestamp_granularities=["word", "segment"],
                )
        except Exception as exc:
            raise ASREngineError(f"whisper_api transcription failed: {exc}") from exc

        raw = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        api_segments = raw.get("segments") or []
        api_words = raw.get("words") or []

        segments: list[ASRSegment] = []
        for i, seg in enumerate(api_segments):
            seg_start, seg_end = seg["start"], seg["end"]
            seg_confidence = math.exp(seg.get("avg_logprob", 0.0)) if "avg_logprob" in seg else 0.5
            seg_words = [
                ASRWord(word=w["word"].strip(), start=w["start"], end=w["end"], confidence=seg_confidence)
                for w in api_words if seg_start <= w["start"] < seg_end
            ]
            segments.append(ASRSegment(
                segment_id=f"seg_{i:05d}", start=seg_start, end=seg_end, text=seg["text"].strip(),
                words=seg_words, avg_confidence=seg_confidence,
                no_speech_prob=seg.get("no_speech_prob"),
            ))

        if not segments and api_words:
            # Some verbose_json responses omit segments entirely; fall back to treating
            # the whole word list as one segment rather than dropping the transcript.
            text = " ".join(w["word"] for w in api_words).strip()
            segments = [ASRSegment(
                segment_id="seg_00000", start=api_words[0]["start"], end=api_words[-1]["end"], text=text,
                words=[ASRWord(word=w["word"].strip(), start=w["start"], end=w["end"], confidence=0.5)
                       for w in api_words],
                avg_confidence=0.5,
            )]

        return ASRResult(
            engine=self.name, model_version=self.model_version,
            language=language or raw.get("language", "und"), segments=segments,
        )
