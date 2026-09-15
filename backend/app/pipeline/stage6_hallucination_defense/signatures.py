"""Regex-based known-hallucination-artifact registry.

Distinct from the acoustic/statistical signals in `defense.py` (confidence, VAD,
compression ratio) — a signature is a *specific known text pattern* a model has been
observed to hallucinate for a given language, documented with the evidence that confirmed
it. New entries should carry a real observed case in `description`, not a guessed pattern —
an unconfirmed guess only weakens the registry's credibility as an audit trail.

A signature's `language` is normally an exact language code, but the literal value "any"
matches regardless of the transcript's detected language. This exists for two credit/
watermark patterns that are language-agnostic by nature rather than needing a growing,
per-language whack-a-mole list: a spoken URL (nobody says one; any language's hallucinated
"visit www.example.com" is equally implausible), and fansub/broadcaster credit vocabulary
that convention keeps in English even in a non-English show (found in production leaking
into a Chinese-audio episode: "Yo-Yo Television Series Exclusive", "MING PAO CANADA
www.MINGPAO.com" — the first two "any"-language entries below).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_SIGNATURES_PATH = Path(__file__).parent / "default_signatures.json"


@dataclass(frozen=True)
class Signature:
    id: str
    language: str
    pattern: re.Pattern
    category: str
    weight: float
    description: str


def load_signatures(path: str | Path | None = None) -> list[Signature]:
    signatures_path = Path(path) if path else _DEFAULT_SIGNATURES_PATH
    if not signatures_path.is_file():
        return []
    data = json.loads(signatures_path.read_text(encoding="utf-8"))
    return [
        Signature(
            id=s["id"], language=s["language"], pattern=re.compile(s["pattern"], re.IGNORECASE),
            category=s["category"], weight=s["weight"], description=s["description"],
        )
        for s in data.get("signatures", [])
    ]


def matching_weight(text: str, language: str, signatures: list[Signature]) -> tuple[float, str | None]:
    """Returns (weight, signature_id) for the highest-weight signature matching this text
    in this language, or (0.0, None) if none match."""
    best_weight = 0.0
    best_id: str | None = None
    for sig in signatures:
        if sig.language != language and sig.language != "any":
            continue
        if sig.pattern.search(text) and sig.weight > best_weight:
            best_weight = sig.weight
            best_id = sig.id
    return best_weight, best_id
