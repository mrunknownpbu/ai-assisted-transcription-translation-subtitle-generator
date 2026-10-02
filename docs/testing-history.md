# Test commands and history (moved verbatim from CLAUDE.md)

`docs/testing.md` is the maintained testing guide; this is the command and count history it replaces.


```bash
cd /opt/projects/subtitle-ai && PYTHONPATH=subtitle_ai uv run --with pytest pytest tests -q
```

Plain `python -m pytest` / `pytest` also work if your environment
already has the right interpreter on `PATH` and deps installed (see
`.github/workflows/test.yml` for the exact CPU-only CI setup) -- the
`uv run` form above is the one that reliably works from a fresh shell.

Lint and types (both run in CI; config in `pyproject.toml`):

```bash
uvx ruff@0.16.10 check subtitle_ai scripts tests
uvx mypy@2.4.0 --python-executable .venv/bin/python
```

mypy checks every module in `subtitle_ai/` with no exclusions (the 12-module
baseline from when it was added was cleared 2026-10-02); new code must pass.

Baseline as of 2026-09-28: 1065 passing, 0 failures (plus 89 frontend
tests -- `cd frontend && npm test -- --run`; up from 939 after the
natural-dialogue plan's five steps -- see the segmentation-naturalness,
turn-detection and ASR-style entries elsewhere in this file). Every
backend test is `unittest`-style, so with no pytest available this also
works:
`cd subtitle_ai && PYTHONPATH=.:../tests ../.venv/bin/python -m unittest discover -s ../tests -t ../tests`.
CI installs an explicit package list (`.github/workflows/test.yml`), not
uv.lock -- a new runtime dependency must be added there too (ruamel.yaml
was, 2026-09-28; numpy was, 2026-09-28, for turns.py -- see pyproject.toml).
Verified directly on the
current host (myphy-ai), not just in CI or inside Docker: 0 skips once
`ffmpeg`, `uv`, and `node`/`npm` are on the bare host too, not just inside
the image -- CI's own setup installs `ffmpeg` as a separate step for the
same reason (see `.github/workflows/test.yml`). (History: 708 after
fixing `test_glossary_profile.py`'s stale `"Eda Yıldız"` canonical
assertion 2026-09-22 -- see `docs/IMPROVEMENT_PLAN.md` section 1.1 -- then 727
after the lightweight-sampler-model tests (2.1), 737 after VRAM
pre-flight (2.2), 757 after orphan-context-padding (3.2), 756 after
removing `tvdb_client.characters()`'s dead-code test (3.3), 768 after the
worker stage/progress tests (4.3), 795 after the batch-queueing and
SRT-editor tests (4.1/4.2), then 830 (2026-09-25) before the run that took
it to 876: WebVTT-as-`.srt` source support, the Series page's folder-name
title fallback and episodes-vs-jobs count fix, `.vtt` uploads, the Jobs
page Refresh-button fix, and `SUBTITLE_AI_COMPUTE_TYPE`; then 939 after
docs/ENHANCEMENT_DRAFT.md's round (2026-09-28). Update this line rather than
leaving it to drift the next time the count moves.)

Frontend: `cd frontend && npx tsc --noEmit && npm test -- --run`.
