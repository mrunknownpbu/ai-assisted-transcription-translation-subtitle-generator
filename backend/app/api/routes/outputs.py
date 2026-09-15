from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.models import Output
from app.db.session import get_session

router = APIRouter(prefix="/api/outputs", tags=["outputs"])

_MEDIA_TYPES = {"srt": "application/x-subrip", "vtt": "text/vtt", "webvtt": "text/vtt", "burned_in": "video/mp4"}


@router.get("/{output_id}/download")
def download_output(output_id: int, session: Session = Depends(get_session)):
    output = session.get(Output, output_id)
    if output is None:
        raise HTTPException(404, "Output not found")
    path = Path(output.file_path)
    if not path.is_file():
        raise HTTPException(410, "Output file is no longer on disk")
    return FileResponse(
        path, media_type=_MEDIA_TYPES.get(output.format, "application/octet-stream"), filename=path.name,
    )


@router.get("/{output_id}/provenance")
def get_output_provenance(output_id: int, session: Session = Depends(get_session)):
    output = session.get(Output, output_id)
    if output is None:
        raise HTTPException(404, "Output not found")
    return output.provenance_json
