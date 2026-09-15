"""Read-only browsing of an optionally-mounted reference-subtitle archive (real human-made
translations for a show, kept outside the media library since it isn't playable media) --
lets the evaluation harness's UI offer a file picker instead of requiring an exact path,
the same way library.py does for registering media. See app/evaluation/ for the scoring
itself and app/api/routes/evaluation.py for where a resolved path here feeds in.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import LibraryEntryOut
from app.config import Settings, get_settings

router = APIRouter(prefix="/api/references", tags=["references"])

_SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa"}


def resolve_reference_path(settings: Settings, relative_path: str) -> Path:
    """Resolves a user-supplied relative path against the configured reference-archive
    root and refuses anything that escapes it -- same convention and same reasoning as
    media.py's resolve_library_path: this is the one place user input becomes a filesystem
    path we didn't choose ourselves."""
    if settings.reference_dir is None:
        raise HTTPException(404, "No reference archive is configured on this deployment")

    reference_root = settings.reference_dir.resolve()
    candidate = (reference_root / relative_path.lstrip("/")).resolve()
    if candidate != reference_root and reference_root not in candidate.parents:
        raise HTTPException(422, "Path escapes the configured reference archive")
    return candidate


@router.get("/browse", response_model=list[LibraryEntryOut])
def browse_references(path: str = "", settings: Settings = Depends(get_settings)):
    directory = resolve_reference_path(settings, path)
    if not directory.is_dir():
        raise HTTPException(422, f"Not a directory: {path}")

    entries: list[LibraryEntryOut] = []
    for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.is_dir():
            entries.append(LibraryEntryOut(name=child.name, path=f"{path.rstrip('/')}/{child.name}".lstrip("/"),
                                            is_dir=True))
        elif child.suffix.lower() in _SUBTITLE_EXTENSIONS:
            entries.append(LibraryEntryOut(name=child.name, path=f"{path.rstrip('/')}/{child.name}".lstrip("/"),
                                            is_dir=False))
    return entries
