"""The single controlled output layer. No other module may write to the
media root -- this is the only place that resolves a path, validates it,
and performs a filesystem write, so every safety guarantee (no path
traversal, no overwriting an external subtitle, no partial write) is
enforced in exactly one place instead of trusted to every caller.
"""

from __future__ import annotations

import os
from pathlib import Path

# Any file matching one of these is categorically off-limits: never
# read as transcription/translation input, never written, renamed, moved,
# or deleted, by any code path, ever -- not even with an explicit
# overwrite flag. This list is the one place that decision is made.
PROTECTED_SUFFIXES = (".en.hi.srt", ".en.forced.srt", ".en.sdh.srt")

# Real gap this closes (production-readiness audit, 2026-09-21): a
# browser-uploaded SRT (api.py's POST /api/srt-uploads) was already
# capped at 2 MiB, but a source_srt_path pointed at the media library
# had no size limit at all -- srt_translation.parse_and_validate() reads
# the whole file into memory before any check runs. Shared here (not
# duplicated) so both paths enforce the identical limit. Generous for
# any real subtitle file.
MAX_SRT_FILE_BYTES = 2 * 1024 * 1024


# The only translation target this deployment supports: translate.load_model()
# pins NLLB's BOS token to eng_Latn, so the model can only emit English.
# Every place that names, validates or defaults a target language uses this
# instead of a literal "en", so they can't drift apart.
TARGET_LANG = "en"


class OutputSafetyError(ValueError):
    pass


def _is_protected(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in PROTECTED_SUFFIXES)


def resolve_media_path(media_root: str | Path, relative_path: str | Path, *,
                       must_exist: bool = False) -> Path:
    """The one place ANY GUI-facing path (browse a directory, inspect a
    video's metadata, enqueue a job) is resolved and confirmed to stay
    inside media_root -- same traversal guarantee resolve_output_path
    gives the write side, reused here for the read side rather than
    reimplemented. An absolute path is accepted only if it already
    resolves inside the root; `../../etc/passwd` or `/etc/passwd` are
    rejected either way."""
    root = Path(media_root).resolve()
    candidate = Path(relative_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise OutputSafetyError(f"path escapes media root: {resolved}") from exc
    if must_exist and not resolved.exists():
        raise OutputSafetyError(f"path does not exist: {resolved}")
    return resolved


def resolve_output_path(media_root: str | Path, video_path: str | Path, language: str) -> Path:
    """The only allowed output shapes are `<video stem>.<language>.srt`
    (source transcript) or `<video stem>.en.srt` (translation) -- never
    an arbitrary filename, and never anything matching PROTECTED_SUFFIXES."""
    root = Path(media_root).resolve()
    try:
        video = resolve_media_path(root, video_path)
    except OutputSafetyError as exc:
        raise OutputSafetyError(f"video path escapes media root: {video_path}") from exc

    if not re_lang_ok(language):
        raise OutputSafetyError(f"invalid language code for output naming: {language!r}")

    target = video.parent / f"{video.stem}.{language}.srt"
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise OutputSafetyError(f"resolved output path escapes media root: {target}") from exc
    if _is_protected(target.name):
        raise OutputSafetyError(f"refusing to target a protected external-subtitle path: {target.name}")
    return target


def re_lang_ok(language: str) -> bool:
    return bool(language) and language.isalpha() and language.islower() and 2 <= len(language) <= 3


def write_srt_atomic(path: str | Path, content: str, *, allow_overwrite: bool) -> bool:
    """temp file -> fsync -> atomic rename. Never a partial write is ever
    visible at `path`: a crash mid-write leaves the temp file, never a
    truncated target. `allow_overwrite=False` (the KEEP case) makes this
    exactly as safe as the prior implementation's O_EXCL create -- refuses
    silently, does not raise, so callers can treat "already exists" as a
    normal outcome rather than an error."""
    target = Path(path)
    if _is_protected(target.name):
        raise OutputSafetyError(f"refusing to write a protected external-subtitle path: {target.name}")
    if target.exists() and not allow_overwrite:
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True
