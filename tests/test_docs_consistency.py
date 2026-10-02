"""Documentation consistency: README.md, .env.example and compose.yml.

Documentation is the one part of this system with no automated gate, and
it is the part that drifts. Three real defects motivated these tests:

1. `SUBTITLE_AI_CODE_SWITCH_DETECTION` was documented in README's tuning
   knobs table but absent from BOTH `.env.example` and `compose.yml`.
   Compose passes only what its `environment:` block lists, so the toggle
   could not be set at all in the bundled deployment -- defeating the
   stated purpose in translate.code_switch_detection_enabled()'s
   docstring ("so a real production regression can be turned off with an
   env var change, not a code revert").
2. `SUBTITLE_AI_COMPUTE_TYPE` was in `.env.example` and `compose.yml` but
   missing from README's table, despite being the one knob a new host
   must set correctly for its GPU generation.
3. Documented defaults silently drifting from the code's own defaults
   (`SUBTITLE_AI_CAST_REFRESH_DAYS` moved 30 -> 0.5 in code while prose
   elsewhere still described the old value).

The env-var inventories are derived from the files themselves rather than
hardcoded, so a new variable is covered automatically."""

import re
import unittest
from pathlib import Path
from unittest.mock import patch

import audio_streams
import cast_enrichment
import gpu
import translate
import workdir

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE = ROOT / "compose.yml"

# `.env.example` lists each variable commented out with its example value.
_ENV_EXAMPLE_VAR = re.compile(r"^\s*#?\s*(SUBTITLE_AI_[A-Z0-9_]+)=", re.M)
# compose.yml forwards operator-settable vars as `VAR: ${VAR:-}`. Keys set
# to a literal container path (SUBTITLE_AI_DB: /cache/jobs.db) are fixed
# deployment wiring, not operator knobs, and deliberately excluded.
_COMPOSE_SUBSTITUTED = re.compile(r"^\s+(SUBTITLE_AI_[A-Z0-9_]+):\s*\$\{", re.M)
_TABLE_VAR = re.compile(r"`(SUBTITLE_AI_[A-Z0-9_]+)`")


def _env_example_vars():
    return set(_ENV_EXAMPLE_VAR.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


def _compose_substituted_vars():
    return set(_COMPOSE_SUBSTITUTED.findall(COMPOSE.read_text(encoding="utf-8")))


def _tuning_knob_rows():
    """The rows of README's "Tuning knobs" table, as raw markdown lines.

    Located by heading rather than line number so the table can move."""
    lines = README.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("###") and "Tuning knobs" in line:
            break
    else:
        raise AssertionError("README.md has no '### Tuning knobs' heading")
    rows = []
    for line in lines[i + 1:]:
        stripped = line.strip()
        if stripped.startswith("|"):
            rows.append(stripped)
        elif rows:
            break
    if not rows:
        raise AssertionError("no table found under README's 'Tuning knobs' heading")
    # Drop the header and the |---|---|---| separator.
    return rows[2:]


class EnvExampleComposeParityTests(unittest.TestCase):
    """Every operator-settable SUBTITLE_AI_* variable must appear in BOTH
    `.env.example` and `compose.yml`'s environment block.

    A variable in `.env.example` but not forwarded by compose is the worst
    shape: it looks settable, the operator sets it, and it silently does
    nothing inside the container because compose never passes it through."""

    def test_every_documented_env_var_is_forwarded_by_compose(self):
        missing = sorted(_env_example_vars() - _compose_substituted_vars())
        self.assertEqual(missing, [], "in .env.example but not forwarded by compose.yml -- "
                                      "setting these would silently do nothing in the container")

    def test_every_forwarded_env_var_is_documented_in_env_example(self):
        missing = sorted(_compose_substituted_vars() - _env_example_vars())
        self.assertEqual(missing, [], "forwarded by compose.yml but absent from .env.example -- "
                                      "an operator has no way to discover these")


class TuningKnobsTableTests(unittest.TestCase):
    """README's "Tuning knobs" table is the documented source of truth for
    every toggle's default (docs/architecture.md's "Feature toggles"
    section points readers at it explicitly). A knob documented there must
    actually be settable.

    Only this direction is asserted. Many SUBTITLE_AI_* variables read in
    code are deployment paths documented elsewhere in the README
    (SUBTITLE_AI_DB, SUBTITLE_AI_WORK_ROOT, ...), and the table also
    carries non-SUBTITLE_AI_ variables (TVDB_API_KEY, SONARR_URL, ...), so
    neither a reverse nor a whole-file assertion would be sound."""

    def test_every_tabled_knob_is_settable(self):
        tabled = set()
        for row in _tuning_knob_rows():
            tabled.update(_TABLE_VAR.findall(row))
        self.assertTrue(tabled, "no SUBTITLE_AI_* variables found in the tuning knobs table")
        for var in sorted(tabled):
            with self.subTest(var=var):
                self.assertIn(var, _env_example_vars(), f"{var} is in README's table but not .env.example")
                self.assertIn(var, _compose_substituted_vars(), f"{var} is in README's table but "
                                                                f"compose.yml does not forward it")


class DocumentedDefaultsTests(unittest.TestCase):
    """README's stated default must match what the code actually defaults to.

    Each case pins both sides to an explicit expectation, so a change to
    either the code or the table breaks the test. The table cannot be
    parsed generically: one row documents two variables at once
    (`SUBTITLE_AI_NLLB_BATCH_SIZE`, `SUBTITLE_AI_NLLB_NUM_BEAMS`), so each
    case asserts its rendering appears in that variable's own row."""

    # (variable, how README renders its default, callable, expected value)
    CASES = [
        ("SUBTITLE_AI_CAST_REFRESH_DAYS", "`0.5`", lambda: cast_enrichment.refresh_days(), 0.5),
        ("SUBTITLE_AI_VRAM_MARGIN_GB", "`3.2`", lambda: gpu._vram_margin_from_env(), 3.2),
        ("SUBTITLE_AI_NLLB_BATCH_SIZE", "`32`", lambda: translate._default_batch_size(), 32),
        ("SUBTITLE_AI_NLLB_NUM_BEAMS", "`2`", lambda: translate._default_num_beams(), 2),
        ("SUBTITLE_AI_MODEL_IDLE_SECONDS", "`600`", lambda: translate.model_idle_seconds_from_env(), 600.0),
        ("SUBTITLE_AI_SAMPLE_MODEL", "`small`", lambda: audio_streams._sample_model_name(), "small"),
        ("SUBTITLE_AI_NLLB_BACKEND", "`ct2`", lambda: translate._default_backend(), "ct2"),
    ]

    # Blanked rather than deleted: every reader .strip()s and treats blank
    # as unset, so this forces the dedicated-GPU path regardless of what
    # the ambient environment or a local .env happens to set.
    BLANKED = {
        "SUBTITLE_AI_GPU_SHARED": "",
        "SUBTITLE_AI_CAST_REFRESH_DAYS": "",
        "SUBTITLE_AI_VRAM_MARGIN_GB": "",
        "SUBTITLE_AI_NLLB_BATCH_SIZE": "",
        "SUBTITLE_AI_NLLB_NUM_BEAMS": "",
        "SUBTITLE_AI_MODEL_IDLE_SECONDS": "",
        "SUBTITLE_AI_SAMPLE_MODEL": "",
        "SUBTITLE_AI_NLLB_BACKEND": "",
    }

    def _row_for(self, var):
        rows = [r for r in _tuning_knob_rows() if f"`{var}`" in r]
        self.assertEqual(len(rows), 1, f"expected exactly one tuning-knobs row mentioning {var}")
        return rows[0]

    def test_code_defaults_match_the_table(self):
        for var, rendering, read_default, expected in self.CASES:
            with self.subTest(var=var):
                with patch.dict("os.environ", self.BLANKED):
                    self.assertEqual(read_default(), expected)
                self.assertIn(rendering, self._row_for(var),
                              f"README's row for {var} does not show its real default {expected!r}")

    def test_failed_work_retention_default_matches_the_table(self):
        # The env read for this one lives in main.py, whose import spawns a
        # background thread -- assert against the constant instead.
        self.assertEqual(workdir.DEFAULT_FAILED_RETENTION_HOURS, 24.0)
        self.assertIn("`24`", self._row_for("SUBTITLE_AI_FAILED_WORK_RETENTION_HOURS"))

    def test_shared_gpu_defaults_match_the_table(self):
        """The table documents the shared-GPU variants in parentheses:
        "`32`, `2` (`8`, `2` when shared)" and "`600` (`0` when shared)"."""
        shared = {**self.BLANKED, "SUBTITLE_AI_GPU_SHARED": "1"}
        with patch.dict("os.environ", shared):
            self.assertEqual(translate._default_batch_size(), 8)
            self.assertEqual(translate._default_num_beams(), 2)
            self.assertEqual(translate.model_idle_seconds_from_env(), 0.0)
        self.assertIn("when shared", self._row_for("SUBTITLE_AI_NLLB_BATCH_SIZE"))
        self.assertIn("when shared", self._row_for("SUBTITLE_AI_MODEL_IDLE_SECONDS"))


if __name__ == "__main__":
    unittest.main()


class ApiDocumentationTests(unittest.TestCase):
    """docs/api.md must list every route the app serves."""

    def test_every_api_route_is_documented(self):
        import tempfile

        import api
        text = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            app = api.create_app(Path(tmp) / "jobs.db", tmp)
        missing = []
        for route in app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/"):
                continue
            for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
                if f"{method} {path}" not in text:
                    missing.append(f"{method} {path}")
        self.assertEqual(missing, [], "routes served but not documented in docs/api.md")
