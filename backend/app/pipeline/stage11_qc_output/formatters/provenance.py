"""Provenance sidecar. SRT has no comment syntax, and even for WebVTT (which does) the
full provenance record can be large — a sidecar JSON file is the one mechanism every
output format gets uniformly, in addition to the inline NOTE block WebVTT also carries.
"""
from __future__ import annotations

import json
from pathlib import Path


def write_sidecar(dest_path: str | Path, provenance: dict) -> Path:
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(provenance, indent=2, default=str), encoding="utf-8")
    return dest
