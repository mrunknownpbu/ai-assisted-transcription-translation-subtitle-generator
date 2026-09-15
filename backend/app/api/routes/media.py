from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.schemas import MediaFileOut, ReferenceOptionsOut, RegisterMediaRequest, SelectStreamRequest
from app.config import Settings, get_settings
from app.db.models import AudioStream, MediaFile
from app.db.session import get_session
from app.pipeline.stage1_inspection.inspector import inspect_media
from app.pipeline.stage2_stream_selection.ranker import rank_streams
from app.pipeline.stage8_translation.tvdb_client import extract_tvdb_id_from_path

router = APIRouter(prefix="/api/media", tags=["media"])


def _register_inspected_media(path: Path, filename: str, session: Session) -> MediaFile:
    """Shared by upload (path = a copy we just made) and library registration (path = the
    real file, read directly, never copied) — everything past "here is a path" is
    identical: inspect, auto-rank streams, persist. Fails loudly (422) if the file has no
    audio at all, since there is nothing for the rest of the pipeline to work with."""
    inspection = inspect_media(path)
    if not inspection.audio_streams:
        raise HTTPException(422, "No audio streams found in this file")

    media = MediaFile(
        original_path=str(path), filename=filename,
        container_format=inspection.container_format, size_bytes=inspection.size_bytes,
        file_hash=inspection.file_hash, raw_ffprobe_json=inspection.raw_ffprobe_json,
        tvdb_id=extract_tvdb_id_from_path(str(path)),
    )
    session.add(media)
    session.flush()

    ranking = rank_streams(path, inspection.audio_streams)
    scores_by_index = {r.stream_index: r for r in ranking.rankings}

    for stream_info in inspection.audio_streams:
        score = scores_by_index[stream_info.stream_index]
        is_selected = stream_info.stream_index == ranking.selected_stream_index
        session.add(AudioStream(
            media_file_id=media.id, stream_index=stream_info.stream_index, codec=stream_info.codec,
            channels=stream_info.channels, sample_rate=stream_info.sample_rate,
            duration_s=stream_info.duration_s, stream_hash=stream_info.stream_hash,
            embedded_language_tag=stream_info.embedded_language_tag,
            dialogue_score=score.dialogue_score, dialogue_rank=ranking.rankings.index(score) + 1,
            dialogue_score_breakdown=score.breakdown,
            selected_by="auto" if is_selected else None,
        ))
    session.commit()
    session.refresh(media)
    return media


@router.post("/upload", response_model=MediaFileOut)
def upload_media(file: UploadFile, session: Session = Depends(get_session), settings: Settings = Depends(get_settings)):
    """Stores the upload read-only under the media volume, then runs Stage 1 inspection
    and Stage 2 auto-ranking immediately so the UI can show stream choices right away."""
    dest_name = f"{uuid.uuid4().hex}_{file.filename}"
    dest_path = Path(settings.media_dir) / dest_name
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    return _register_inspected_media(dest_path, file.filename or dest_name, session)


def resolve_library_path(settings: Settings, relative_path: str) -> Path:
    """Resolves a user-supplied relative path against the configured library root and
    refuses anything that escapes it — the one place in this router where user input
    becomes a filesystem path we didn't choose ourselves."""
    if settings.library_dir is None:
        raise HTTPException(404, "No media library is configured on this deployment")

    library_root = settings.library_dir.resolve()
    candidate = (library_root / relative_path.lstrip("/")).resolve()
    if candidate != library_root and library_root not in candidate.parents:
        raise HTTPException(422, "Path escapes the configured media library")
    return candidate


@router.post("/register", response_model=MediaFileOut)
def register_library_media(
    body: RegisterMediaRequest, session: Session = Depends(get_session), settings: Settings = Depends(get_settings),
):
    """Registers a file that already exists in the mounted media library, in place — no
    copy is ever made. Strictly more read-only than /upload: there isn't even a copy step
    between "here is the source" and inspecting it."""
    path = resolve_library_path(settings, body.path)
    if not path.is_file():
        raise HTTPException(404, f"No such file in the media library: {body.path}")

    return _register_inspected_media(path, path.name, session)


@router.get("", response_model=list[MediaFileOut])
def list_media(session: Session = Depends(get_session)):
    return session.query(MediaFile).order_by(MediaFile.inspected_at.desc()).all()


_SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa"}


@router.get("/{media_id}/reference-options", response_model=ReferenceOptionsOut)
def get_reference_options(media_id: str, session: Session = Depends(get_session), settings: Settings = Depends(get_settings)):
    """Powers the evaluation panel's dropdowns: real embedded subtitle streams (read
    straight out of the ffprobe data already captured at registration -- no new ffmpeg
    call) and any sidecar subtitle files sitting next to the source in the library,
    instead of making the user type a stream index or a full relative path by hand."""
    media = session.get(MediaFile, media_id)
    if media is None:
        raise HTTPException(404, "Media file not found")

    embedded = [
        {"index": s["index"], "language": (s.get("tags") or {}).get("language"), "codec_name": s.get("codec_name")}
        for s in media.raw_ffprobe_json.get("streams", [])
        if s.get("codec_type") == "subtitle"
    ]

    siblings = []
    if settings.library_dir is not None:
        try:
            library_root = settings.library_dir.resolve()
            media_path = Path(media.original_path).resolve()
            if library_root in media_path.parents:
                for f in sorted(media_path.parent.iterdir()):
                    # Filtered to this episode's own stem, not every subtitle in the season
                    # folder -- a season directory routinely holds one sidecar per episode,
                    # and offering all of them would make it easy to score against the
                    # wrong episode's reference by mistake.
                    if f.is_file() and f.suffix.lower() in _SUBTITLE_EXTENSIONS and f.name.startswith(media_path.stem):
                        siblings.append({"name": f.name, "path": str(f.relative_to(library_root))})
        except OSError:
            pass  # best-effort discovery only -- the evaluation panel still accepts a manually typed path

    return ReferenceOptionsOut(embedded_subtitle_streams=embedded, sibling_subtitle_files=siblings)


@router.get("/{media_id}", response_model=MediaFileOut)
def get_media(media_id: str, session: Session = Depends(get_session)):
    media = session.get(MediaFile, media_id)
    if media is None:
        raise HTTPException(404, "Media file not found")
    return media


@router.post("/{media_id}/select-stream", response_model=MediaFileOut)
def select_stream(media_id: str, body: SelectStreamRequest, session: Session = Depends(get_session)):
    media = session.get(MediaFile, media_id)
    if media is None:
        raise HTTPException(404, "Media file not found")

    streams = session.query(AudioStream).filter_by(media_file_id=media_id).all()
    valid_indices = {s.stream_index for s in streams}
    if body.stream_index not in valid_indices:
        raise HTTPException(422, f"stream_index {body.stream_index} not found on this media file")

    for s in streams:
        s.selected_by = "manual" if s.stream_index == body.stream_index else None
    session.commit()
    session.refresh(media)
    return media
