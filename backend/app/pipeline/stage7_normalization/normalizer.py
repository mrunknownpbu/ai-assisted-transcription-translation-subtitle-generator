"""Stage 7: Normalization.

Conservative, provenance-aware orthographic cleanup only — never rewrites wording,
removes filler words, or "improves" phrasing, since that would destroy the original
utterance record Stage 5 preserved. Every segment keeps its untouched `text` field
alongside the new `normalized_text`; nothing here is destructive or irreversible.

Rules are split into a universal whitespace/punctuation pass (safe for any language) and
an optional per-language ruleset (only standalone-pronoun capitalization for English so
far) — deliberately small and explicit rather than a general "autocorrect", per the
conservative-corrections-only constraint.
"""
from __future__ import annotations

import re

from app.pipeline.interfaces import CanonicalTranscript

_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.!?;:])")
_MISSING_SPACE_AFTER_PUNCT_RE = re.compile(r"([,.!?;:])(?=[^\s\d])")


def _universal_whitespace_punctuation_cleanup(text: str) -> str:
    text = text.strip()
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _MISSING_SPACE_AFTER_PUNCT_RE.sub(r"\1 ", text)
    return text


def _english_standalone_i_capitalization(text: str) -> str:
    return re.sub(r"(?<![\w'])i(?![\w'])", "I", text)


_LANGUAGE_RULES = {
    "en": [_english_standalone_i_capitalization],
}


def normalize_text(text: str, language: str) -> str:
    normalized = _universal_whitespace_punctuation_cleanup(text)
    for rule in _LANGUAGE_RULES.get(language, []):
        normalized = rule(normalized)
    return normalized


def normalize_transcript(transcript: CanonicalTranscript, language: str) -> CanonicalTranscript:
    for segment in transcript.segments:
        segment.normalized_text = normalize_text(segment.text, language)
    return transcript
