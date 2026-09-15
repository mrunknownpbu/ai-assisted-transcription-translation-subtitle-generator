import pytest

from app.pipeline.interfaces import TranslatedChunk
from app.pipeline.stage11_qc_output import qc_translation


def _chunk(chunk_id, source, translated):
    return TranslatedChunk(chunk_id=chunk_id, target_language="es", source_text=source, translated_text=translated,
                            engine="fake", model_version="fake:1", word_span=(0.0, 1.0))


@pytest.mark.unit
def test_healthy_translation_passes_all_checks():
    report = qc_translation.run([_chunk("c1", "Hello there, how are you today?", "Hola, ¿cómo estás hoy?")])
    assert report.passed is True


@pytest.mark.unit
def test_empty_translation_fails():
    report = qc_translation.run([_chunk("c1", "Hello", "")])
    assert report.passed is False
    assert any(f.check == "translation_non_empty" and not f.passed for f in report.findings)


@pytest.mark.unit
def test_leaked_glossary_placeholder_fails():
    report = qc_translation.run([_chunk("c1", "Eda said hi", "Xab dijo hola")])
    assert report.passed is False
    assert any(f.check == "translation_no_leaked_placeholder" for f in report.findings)


@pytest.mark.unit
def test_unbalanced_brackets_fails():
    report = qc_translation.run([_chunk("c1", "(hello)", "(hola")])
    assert report.passed is False
    assert any(f.check == "translation_balanced_brackets" for f in report.findings)


@pytest.mark.unit
def test_suspiciously_short_translation_fails_only_above_min_source_length():
    long_source = "This is a reasonably long source sentence that should translate to something substantial."
    report = qc_translation.run([_chunk("c1", long_source, "Si.")])
    assert any(f.check == "translation_length_ratio" for f in report.findings)

    # A short source is exempt from the ratio check entirely (would false-positive otherwise).
    report_short = qc_translation.run([_chunk("c1", "Hi", "S")])
    assert not any(f.check == "translation_length_ratio" for f in report_short.findings)


@pytest.mark.unit
def test_suspiciously_long_translation_fails():
    report = qc_translation.run([_chunk("c1", "Hi there", "This translation is absurdly long for such a short source phrase indeed")])
    assert any(f.check == "translation_length_ratio" for f in report.findings)


@pytest.mark.unit
def test_degenerate_repetition_detected():
    looped = "if you are a zombie you are a zombie if you are a zombie you are a zombie"
    report = qc_translation.run([_chunk("c1", "some source text about zombies", looped)])
    assert any(f.check == "translation_no_degenerate_repetition" for f in report.findings)


@pytest.mark.unit
def test_cross_chunk_duplicate_from_different_source_is_flagged():
    duplicated_translation = "Everything is going to be fine, I promise."
    chunks = [
        _chunk("c1", "The weather is nice today.", duplicated_translation),
        _chunk("c2", "I am going to the store now.", duplicated_translation),
    ]
    report = qc_translation.run(chunks)
    assert any(f.check == "translation_no_cross_chunk_duplicate" for f in report.findings)


@pytest.mark.unit
def test_identical_source_producing_identical_translation_is_not_flagged_as_duplicate():
    same_translation = "Everything is going to be fine, I promise."
    chunks = [_chunk("c1", "Same source line.", same_translation), _chunk("c2", "Same source line.", same_translation)]
    report = qc_translation.run(chunks)
    assert not any(f.check == "translation_no_cross_chunk_duplicate" for f in report.findings)
