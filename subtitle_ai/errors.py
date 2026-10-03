"""Typed application errors with stable codes.

Every failure a job can end in has a `code` (stored as the job's
`error_category`, shown in the UI and sent in the failure webhook), the
pipeline `stage` it belongs to, and optionally a `remediation` hint. Codes
are a public contract: never rename one, only add. Anything not derived from
`SubtitleAiError` is an unexpected defect and is reported as `PIPELINE_ERROR`
with its traceback kept in the logs, never shown to the user.

Existing exception classes keep their old names and import locations (each
module re-exports the class from here), so `except` clauses elsewhere are
unaffected. This module imports nothing from the application.
"""

from __future__ import annotations

from dataclasses import dataclass

UNEXPECTED_CODE = "PIPELINE_ERROR"
UNEXPECTED_MESSAGE_LIMIT = 500


class SubtitleAiError(Exception):
    code = UNEXPECTED_CODE
    stage: str | None = None
    remediation: str | None = None


# --- input / media ----------------------------------------------------------

class MediaError(SubtitleAiError, RuntimeError):
    """The media file could not be inspected or its audio extracted."""
    code = "MEDIA_ERROR"
    stage = "media_inspection"
    remediation = "Check that the file exists and plays in ffprobe/ffmpeg."


class SubtitleParseError(SubtitleAiError, ValueError):
    """The source subtitle is malformed in a way a translation job refuses to
    silently tolerate (a lenient parse would translate a truncated subset)."""
    code = "SRT_VALIDATION_ERROR"
    stage = "subtitle_parsing"
    remediation = "Fix or replace the source subtitle file."


class RetimeRefusedError(SubtitleAiError, ValueError):
    """A subtitle shares too little text with the video's audio to be re-timed
    on evidence (wrong episode, wrong language, or a very different edit)."""
    code = "RETIME_REFUSED"
    stage = "retiming"
    remediation = ("Check that the subtitle belongs to this video and that its language matches; "
                   "nothing was written.")


# --- language ---------------------------------------------------------------

class LowConfidenceLanguageError(SubtitleAiError, RuntimeError):
    """source_lang=AUTO and the detected language's confidence is below the
    threshold with low_confidence_action="require_override"."""
    code = "LOW_CONFIDENCE_LANGUAGE"
    stage = "language_detection"
    remediation = "Choose the source language manually and retry."


class UnsupportedLanguageError(SubtitleAiError, RuntimeError):
    """The resolved source language has no configured translation mapping."""
    code = "UNSUPPORTED_LANGUAGE"
    stage = "translation"
    remediation = "Pick a supported source language."


# --- output -----------------------------------------------------------------

class OutputSafetyError(SubtitleAiError, ValueError):
    """A path escapes the media root, targets a protected file, or an output
    could not be written safely. Existing outputs are left untouched."""
    code = "OUTPUT_ERROR"
    stage = "output"


class OutputWriteError(SubtitleAiError, OSError):
    """A subtitle could not be written (permissions, full disk, vanished mount).
    The write is atomic, so any existing subtitle is untouched."""
    code = "OUTPUT_ERROR"
    stage = "output"
    remediation = ("Check write permission and free space on the media folder; "
                   "the existing subtitle was left untouched.")


# --- GPU --------------------------------------------------------------------

class GpuResourceError(SubtitleAiError):
    code = "GPU_RESOURCE_ERROR"
    stage = "gpu"
    remediation = "Free GPU memory (other processes, resident models) and retry."


class InsufficientVramError(GpuResourceError, RuntimeError):
    """Free VRAM stayed below the required margin for the whole wait window;
    a model load that would very likely CUDA-OOM is refused up front."""


class GpuLockTimeout(GpuResourceError, TimeoutError):
    """A bounded GPU lock acquisition could not proceed."""


# --- reporting --------------------------------------------------------------

@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    stage: str | None
    remediation: str | None
    expected: bool  # False -> a defect: log the traceback, hide it from the user


def describe(exc: BaseException) -> ErrorInfo:
    if isinstance(exc, SubtitleAiError):
        return ErrorInfo(exc.code, str(exc), exc.stage, exc.remediation, expected=True)
    return ErrorInfo(UNEXPECTED_CODE, str(exc)[:UNEXPECTED_MESSAGE_LIMIT], None, None, expected=False)
