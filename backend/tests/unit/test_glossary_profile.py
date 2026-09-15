import pytest

from app.pipeline.stage8_translation.glossary_profile import load_profile_entities


@pytest.mark.unit
def test_missing_directory_yields_no_entities(tmp_path):
    assert load_profile_entities(tmp_path / "does-not-exist", tvdb_id=123) == []


@pytest.mark.unit
def test_global_layer_applies_regardless_of_tvdb_id(tmp_path):
    (tmp_path / "global.yaml").write_text("""
entities:
  - canonical: Kaan
    protected: true
    aliases: [Kaan Bey]
""")
    entities = load_profile_entities(tmp_path, tvdb_id=999)
    assert len(entities) == 1
    assert entities[0].canonical == "Kaan"
    assert entities[0].surface_forms == ["Kaan", "Kaan Bey"]


@pytest.mark.unit
def test_unprotected_entries_are_documentation_only_and_excluded(tmp_path):
    (tmp_path / "global.yaml").write_text("""
entities:
  - canonical: abi
    protected: false
    notes: "context-sensitive, never blindly substituted"
""")
    assert load_profile_entities(tmp_path, tvdb_id=None) == []


@pytest.mark.unit
def test_series_layer_excluded_when_tvdb_id_does_not_match(tmp_path):
    """Regression case straight from the source project: a file must be classified as
    series-specific by its declared tvdb_id key, never by filename convention -- otherwise
    an unrelated series' glossary could leak in."""
    (tmp_path / "some-other-show.yaml").write_text("""
tvdb_id: 111111
entities:
  - canonical: Wrong Show Character
    protected: true
""")
    assert load_profile_entities(tmp_path, tvdb_id=222222) == []
    assert load_profile_entities(tmp_path, tvdb_id=None) == []


@pytest.mark.unit
def test_series_layer_overrides_global_layer_on_canonical_collision(tmp_path):
    (tmp_path / "global.yaml").write_text("""
entities:
  - canonical: Eda
    protected: true
    aliases: []
""")
    (tmp_path / "show.yaml").write_text("""
tvdb_id: 383383
entities:
  - canonical: Eda
    protected: true
    aliases: [Eda Yıldız, Edacım]
""")
    entities = load_profile_entities(tmp_path, tvdb_id=383383)
    assert len(entities) == 1
    assert entities[0].surface_forms == ["Eda", "Eda Yıldız", "Edacım"]


@pytest.mark.unit
def test_real_sen_cal_kapimi_profile_protects_evren_and_yildiz(tmp_path):
    """Uses the exact real file that surfaced this session's 'Evren' -> 'Mr. Universe' bug
    (ported from /opt/docker/appdata/subtitle-ai/glossary/) as a regression fixture."""
    (tmp_path / "series.yaml").write_text("""
tvdb_id: 383383
title: "Love Is In The Air (Sen Çal Kapımı)"
entities:
  - canonical: Eda Yıldız
    protected: true
    aliases: [Eda Yildiz, Eda]
    notes: "must not translate as a common noun"
  - canonical: Evren
    protected: true
    aliases: []
    notes: "observed failure: Evren -> Mr. Universe"
""")
    entities = load_profile_entities(tmp_path, tvdb_id=383383)
    by_canonical = {e.canonical: e for e in entities}
    assert set(by_canonical) == {"Eda Yıldız", "Evren"}
    assert by_canonical["Evren"].surface_forms == ["Evren"]


@pytest.mark.unit
def test_corrupt_yaml_file_is_skipped_not_fatal(tmp_path):
    (tmp_path / "broken.yaml").write_text("entities: [this is: not: valid: yaml:")
    (tmp_path / "good.yaml").write_text("""
entities:
  - canonical: Fine
    protected: true
""")
    entities = load_profile_entities(tmp_path, tvdb_id=None)
    assert [e.canonical for e in entities] == ["Fine"]
