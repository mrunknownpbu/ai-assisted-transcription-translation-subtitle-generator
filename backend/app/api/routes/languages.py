from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import LanguageDetectionOut
from app.db.models import AudioStream, LanguageDetection, MediaFile
from app.db.session import get_session
from app.pipeline.stage3_language_detection.detector import detect_language

router = APIRouter(prefix="/api/media", tags=["languages"])


_MIN_CACHEABLE_CONFIDENCE = 0.5  # a low-confidence result is worth re-verifying, not trusting forever


@router.post("/{media_id}/streams/{stream_id}/detect-language", response_model=LanguageDetectionOut)
def detect_stream_language(media_id: str, stream_id: str, session: Session = Depends(get_session)):
    """Cached strictly on the audio stream's content hash (see AudioStream.stream_hash) —
    re-running detection on the same underlying audio, even under a renamed file, reuses
    the cached result instead of re-decoding. A low-confidence cached result is not
    trusted indefinitely — it's re-run instead, since low confidence is exactly the
    signal that the sample used wasn't representative (e.g. a since-fixed single-window
    detection, or genuinely ambiguous audio worth a fresh multi-window attempt)."""
    media = session.get(MediaFile, media_id)
    stream = session.get(AudioStream, stream_id)
    if media is None or stream is None or stream.media_file_id != media_id:
        raise HTTPException(404, "Media file or audio stream not found")

    cached = (
        session.query(LanguageDetection)
        .filter_by(stream_hash=stream.stream_hash)
        .order_by(LanguageDetection.created_at.desc())
        .first()
    )
    if cached is not None and cached.confidence >= _MIN_CACHEABLE_CONFIDENCE:
        return LanguageDetectionOut(
            detected_language=cached.detected_language, confidence=cached.confidence,
            method=cached.method, candidates=[],
        )

    result = detect_language(media.original_path, stream.stream_index, duration_s=stream.duration_s)
    session.add(LanguageDetection(
        audio_stream_id=stream.id, stream_hash=stream.stream_hash,
        detected_language=result.detected_language, confidence=result.confidence, method=result.method,
    ))
    session.commit()
    return LanguageDetectionOut(
        detected_language=result.detected_language, confidence=result.confidence,
        method=result.method, candidates=result.candidates,
    )
