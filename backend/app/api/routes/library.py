"""Read-only browsing of an optionally-mounted media library, so the GUI can offer a file
picker instead of requiring the user to type an exact path for `POST /api/media/register`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.media import resolve_library_path
from app.api.schemas import LibraryEntryOut
from app.config import Settings, get_settings

router = APIRouter(prefix="/api/library", tags=["library"])

_MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".m4v", ".ts", ".webm", ".mpg", ".mpeg",
}


@router.get("/browse", response_model=list[LibraryEntryOut])
def browse_library(path: str = "", settings: Settings = Depends(get_settings)):
    directory = resolve_library_path(settings, path)
    if not directory.is_dir():
        raise HTTPException(422, f"Not a directory: {path}")

    entries: list[LibraryEntryOut] = []
    for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.is_dir():
            entries.append(LibraryEntryOut(name=child.name, path=f"{path.rstrip('/')}/{child.name}".lstrip("/"),
                                            is_dir=True))
        elif child.suffix.lower() in _MEDIA_EXTENSIONS:
            entries.append(LibraryEntryOut(name=child.name, path=f"{path.rstrip('/')}/{child.name}".lstrip("/"),
                                            is_dir=False))
    return entries
