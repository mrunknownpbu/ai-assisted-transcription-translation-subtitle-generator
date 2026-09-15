import pytest

from app.pipeline.stage8_translation.glossary import (
    Entity, build_glossary, entity_occurrence_report, protect, recover_dropped_entities, restore,
)


@pytest.mark.unit
def test_protect_and_restore_round_trip():
    glossary = build_glossary([Entity(canonical="Eda", surface_forms=["Eda"])])
    text = "Eda walked in. Eda smiled."

    protected = protect(text, glossary)
    assert "Eda" not in protected  # replaced with an opaque placeholder

    restored = restore(protected, glossary)
    assert restored == text


@pytest.mark.unit
def test_every_surface_form_restores_to_the_canonical_name():
    """An alias (e.g. a first name) and the full canonical name are each protected with
    their own placeholder, but both always restore back to the single canonical spelling —
    protection's job is guaranteeing the right name appears, not preserving which alias the
    source happened to use."""
    glossary = build_glossary([Entity(canonical="Eda Yildiz", surface_forms=["Eda Yildiz", "Eda"])])
    text = "Eda Yildiz walked in. Eda smiled."

    restored = restore(protect(text, glossary), glossary)

    assert restored == "Eda Yildiz walked in. Eda Yildiz smiled."


@pytest.mark.unit
def test_longest_surface_form_matched_first_avoids_partial_shadowing():
    glossary = build_glossary([Entity(canonical="Eda Yildiz", surface_forms=["Eda Yildiz", "Eda"])])
    protected = protect("Eda Yildiz is here", glossary)
    # "Eda Yildiz" must be protected as one unit, not as "Eda" + literal " Yildiz" left over.
    assert "Yildiz" not in protected
    assert restore(protected, glossary) == "Eda Yildiz is here"


@pytest.mark.unit
def test_boundary_is_ascii_aware_not_unicode_word_aware():
    """The custom `_bounded` boundary (not Python's `\\b`) is what makes this safe against
    scripts with no whitespace, like Japanese, where `\\b` can match mid-character-run."""
    glossary = build_glossary([Entity(canonical="Tanaka", surface_forms=["Tanaka"])])
    text = "田中Tanakaさん"  # CJK characters directly adjacent to the Latin name, no spaces
    protected = protect(text, glossary)
    assert "Tanaka" not in protected
    assert restore(protected, glossary) == text


@pytest.mark.unit
def test_case_insensitive_matching():
    glossary = build_glossary([Entity(canonical="Eda", surface_forms=["Eda"])])
    protected = protect("EDA walked in with eda.", glossary)
    assert "eda" not in protected.lower()


@pytest.mark.unit
def test_entity_occurrence_report_counts_source_and_target():
    glossary = build_glossary([Entity(canonical="Eda", surface_forms=["Eda"])])
    source_protected = protect("Eda said hi. Eda left.", glossary)
    target_with_both_restored = "Eda said hi. Eda left."  # simulate a perfect restore

    report = entity_occurrence_report(source_protected, target_with_both_restored, glossary)

    assert report["Eda"]["source_count"] == 2
    assert report["Eda"]["target_count"] == 2


@pytest.mark.unit
def test_recover_dropped_entities_reinserts_missing_name_without_calling_a_model():
    glossary = build_glossary([Entity(canonical="Eda", surface_forms=["Eda"])])
    source_protected = protect("Eda said hi. Eda left.", glossary)
    # Simulate a translation that dropped the second occurrence of the name entirely.
    under_translated = "Eda said hi. left."

    recovered = recover_dropped_entities(source_protected, under_translated, glossary)

    assert recovered.count("Eda") == 2


@pytest.mark.unit
def test_recover_dropped_entities_is_a_no_op_when_counts_already_match():
    glossary = build_glossary([Entity(canonical="Eda", surface_forms=["Eda"])])
    source_protected = protect("Eda said hi.", glossary)
    fully_translated = "Eda said hi."

    assert recover_dropped_entities(source_protected, fully_translated, glossary) == fully_translated


@pytest.mark.unit
def test_empty_glossary_is_a_no_op():
    assert protect("hello world", {}) == "hello world"
    assert restore("hello world", {}) == "hello world"
