import pytest

from app.pipeline.stage8_translation.glossary import Entity, build_glossary, protect
from app.pipeline.stage11_qc_output import qc_entity


@pytest.mark.unit
def test_matching_entity_counts_pass():
    glossary = build_glossary([Entity(canonical="Eda", surface_forms=["Eda"])])
    source_protected = protect("Eda said hi. Eda left.", glossary)

    report = qc_entity.run(source_protected, "Eda said hi. Eda left.", glossary)

    assert report.passed is True
    assert all(f.passed for f in report.findings)


@pytest.mark.unit
def test_mismatched_entity_count_fails():
    glossary = build_glossary([Entity(canonical="Eda", surface_forms=["Eda"])])
    source_protected = protect("Eda said hi. Eda left.", glossary)

    report = qc_entity.run(source_protected, "Eda said hi. left.", glossary)  # second occurrence dropped

    assert report.passed is False
    assert any(not f.passed for f in report.findings)


@pytest.mark.unit
def test_empty_glossary_produces_no_findings():
    report = qc_entity.run("some text", "some translated text", {})
    assert report.passed is True
    assert report.findings == []
