"""_resolve_entities/_build_glossary_map/_build_asr_hotwords are pure functions of a
job's `glossary_entities`/`tvdb_id` fields -- exercised directly against a bare Job
instance (never persisted), with TVDB calls mocked, rather than through a full pipeline
run."""
from types import SimpleNamespace

import pytest

from app.db.models import Job
from app.hardware import AcceleratorVendor, HardwareProfile
from app.jobs.worker import JobRunner


def _runner() -> JobRunner:
    hardware = HardwareProfile(vendor=AcceleratorVendor.CPU, gpu_count=0)
    settings = SimpleNamespace()
    return JobRunner(settings=settings, hardware_profile=hardware)


def _job(glossary_entities=None, tvdb_id=None) -> Job:
    return Job(id="job-1", glossary_entities=glossary_entities, tvdb_id=tvdb_id)


@pytest.mark.unit
def test_no_glossary_and_no_tvdb_id_yields_empty_map_and_no_hotwords():
    runner = _runner()
    job = _job()
    assert runner._build_glossary_map(job) == {}
    assert runner._build_asr_hotwords(job) is None


@pytest.mark.unit
def test_reads_surface_forms_key_from_tvdb_or_profile_derived_entries():
    """Regression test: entries the API route derives from TVDB or a local glossary
    profile carry a `surface_forms` key (see routes/jobs.py::_resolve_glossary_entities),
    not `aliases`. Reading only `aliases` silently dropped every such entry's aliases,
    falling back to canonical-name-only matching -- invisible in practice only because a
    redundant live TVDB re-fetch happened to re-add TVDB-sourced aliases; a local-only
    profile entry (like the real 'Evren'/'Cenk' case, both alias-less) never got that
    safety net."""
    runner = _runner()
    job = _job(glossary_entities=[
        {"canonical": "Melek Yücel", "surface_forms": ["Melek Yücel", "Melo"]},
    ])
    glossary_map = runner._build_glossary_map(job)
    assert "melo" in glossary_map
    assert glossary_map["melo"][1] == "Melek Yücel"


@pytest.mark.unit
def test_still_reads_aliases_key_from_explicit_per_job_entries():
    runner = _runner()
    job = _job(glossary_entities=[
        {"canonical": "Serkan Bolat", "aliases": ["Serkan Bolat", "Serkan"]},
    ])
    glossary_map = runner._build_glossary_map(job)
    assert "serkan" in glossary_map
    assert glossary_map["serkan"][1] == "Serkan Bolat"


@pytest.mark.unit
def test_asr_hotwords_includes_every_surface_form_not_just_canonical():
    runner = _runner()
    job = _job(glossary_entities=[
        {"canonical": "Melek Yücel", "surface_forms": ["Melek Yücel", "Melo"]},
        {"canonical": "Evren", "surface_forms": []},
    ])
    hotwords = runner._build_asr_hotwords(job)
    names = set(hotwords.split(", "))
    assert names == {"Melek Yücel", "Melo", "Evren"}


@pytest.mark.unit
def test_asr_hotwords_deduplicates_repeated_names():
    runner = _runner()
    job = _job(glossary_entities=[
        {"canonical": "Evren", "surface_forms": ["Evren"]},
    ])
    hotwords = runner._build_asr_hotwords(job)
    assert hotwords == "Evren"


@pytest.mark.unit
def test_tvdb_enrichment_failure_does_not_raise(monkeypatch):
    def boom(tvdb_id):
        raise RuntimeError("network down")
    monkeypatch.setattr("app.pipeline.stage8_translation.tvdb_client.configured", lambda: True)
    monkeypatch.setattr("app.pipeline.stage8_translation.tvdb_client.glossary_from_characters", boom)

    runner = _runner()
    job = _job(tvdb_id=383383)
    # Must not raise -- a job's glossary enrichment is best-effort, never fatal.
    assert runner._build_glossary_map(job) == {}
    assert runner._build_asr_hotwords(job) is None


@pytest.mark.unit
def test_tvdb_entities_feed_both_translation_glossary_and_asr_hotwords(monkeypatch):
    from app.pipeline.stage8_translation.glossary import Entity

    monkeypatch.setattr("app.pipeline.stage8_translation.tvdb_client.configured", lambda: True)
    monkeypatch.setattr(
        "app.pipeline.stage8_translation.tvdb_client.glossary_from_characters",
        lambda tvdb_id: [Entity(canonical="Eda Yıldız", surface_forms=["Eda Yıldız", "Eda"])],
    )

    runner = _runner()
    job = _job(tvdb_id=383383)
    glossary_map = runner._build_glossary_map(job)
    assert "eda" in glossary_map
    hotwords = runner._build_asr_hotwords(job)
    assert "Eda Yıldız" in hotwords and "Eda" in hotwords.split(", ")
