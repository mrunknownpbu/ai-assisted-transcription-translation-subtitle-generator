"""Loader and checker for tests/fixtures/timing/manifest.json (real pairs)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "timing"
CLASSIFICATIONS = {"constant_offset", "drift_or_nonlinear"}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class TimingCase:
    name: str
    reference: Path
    candidate: Path
    classification: str
    median_time_offset: float | None
    tolerance: float
    source: str


def load_cases(directory: Path = FIXTURE_DIR) -> list[TimingCase]:
    manifest = directory / "manifest.json"
    if not manifest.is_file():
        return []
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    cases = []
    for entry in raw.get("cases", []):
        name = entry.get("name")
        if not name:
            raise ManifestError("every case needs a name")
        for key in ("reference", "candidate", "source"):
            if not entry.get(key):
                raise ManifestError(f"{name}: missing {key!r}")
        expected = entry.get("expected") or {}
        if expected.get("classification") not in CLASSIFICATIONS:
            raise ManifestError(f"{name}: expected.classification must be one of {sorted(CLASSIFICATIONS)}")
        offset = expected.get("median_time_offset")
        if offset is not None and "tolerance" not in expected:
            raise ManifestError(f"{name}: median_time_offset needs an explicit tolerance")
        paths = {key: directory / entry[key] for key in ("reference", "candidate")}
        for key, path in paths.items():
            if not path.is_file():
                raise ManifestError(f"{name}: {key} file not found: {path.name}")
        cases.append(TimingCase(name, paths["reference"], paths["candidate"], expected["classification"],
                                None if offset is None else float(offset),
                                float(expected.get("tolerance", 0.0)), entry["source"]))
    return cases


def check(case: TimingCase, timing: dict[str, Any]) -> list[str]:
    """Reasons the measured timing disagrees with the case; empty = it holds."""
    problems = []
    if timing["classification"] != case.classification:
        problems.append(f"classification {timing['classification']!r}, expected {case.classification!r}")
    if case.median_time_offset is not None:
        median = timing["median_time_offset"]
        if median is None or abs(median - case.median_time_offset) > case.tolerance:
            problems.append(f"median offset {median}, expected {case.median_time_offset} +/- {case.tolerance}")
    return problems
