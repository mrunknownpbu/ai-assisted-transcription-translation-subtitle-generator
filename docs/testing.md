# Testing

## Commands

```bash
# Backend: every test is unittest-style (pytest also runs them)
cd subtitle_ai && PYTHONPATH=.:../tests ../.venv/bin/python -m unittest discover -s ../tests -t ../tests
# one module or class
cd subtitle_ai && PYTHONPATH=.:../tests ../.venv/bin/python -m unittest test_workflow_b_pipeline

# Lint and types (no exclusions)
uvx ruff@0.16.10 check subtitle_ai scripts tests
uvx mypy@2.4.0 --python-executable .venv/bin/python

# Frontend
cd frontend && npx tsc --noEmit && npm test && npm run build
```

CI (`.github/workflows/test.yml`) runs all of the above on every push and pull
request: a `lint` job (ruff), a `test` job (locked CPU-only dependencies, ffmpeg,
mypy, backend tests) and a `frontend` job (typecheck, tests, build). A `docker` job lints the Dockerfile and builds its
frontend stage; the full CUDA image (about 10 GB) is built and exercised by hand
(`docs/deployment.md`).

Command and count history from before 2026-10-02 is in `docs/testing-history.md`.

## Current state (2026-10-02)

| Suite | Count | Notes |
|---|---|---|
| Backend | 1,358 tests, 1 skipped | The skip is the real timing pair that does not exist yet. |
| Frontend | 98 tests in 13 files | Vitest, Testing Library, jsdom. |
| Backend line coverage | 90% (6,254 statements, 634 missed) | Measured with `coverage` over the unittest run; not enforced in CI. |

Weakest backend coverage: `main.py` 0% (process wiring, exercised only by running
the container), `cast_metadata.py` 36%, `reference_aligner.py` 70%, `media.py` 73%,
`qc/output_qc.py` 81%. `srt_translation.py` was 54% until 2026-10-02 because its
pipeline function had no direct tests (the worker tests patch it); it is 96% now.

## Layers

| Layer | What it covers | Where |
|---|---|---|
| Unit | Pure logic: glossary matching, segmentation, QC rules, name correction, SRT parsing, error mapping | most `tests/test_*.py` |
| Integration | Real SQLite store, real ffmpeg on generated audio, real filesystem and atomic writes | `test_jobstore`, `test_worker`, `test_pipeline_*`, `test_workflow_*` |
| Contract | The API surface and its documentation: every route needs the key, every route is documented, env vars are in `.env.example`/compose/README | `test_api`, `test_docs_consistency` |
| Pipeline | A whole workflow with ASR and translation faked | `test_workflow_a_contracts`, `test_workflow_b_pipeline`, `test_pipeline_*` |
| API | FastAPI `TestClient` against a temp store | `test_api` |
| Frontend | Components, hooks, pages, design tokens | `frontend/src/**/*.test.ts(x)` |
| Regression | Fixed bugs and measured behaviours, each citing its decision record | throughout; timing in `test_eval_srt_quality`, `test_timing_reference_pairs` |
| End to end | A real job in the running container | manual, `docs/deployment.md` (not automated) |

## What the two workflows must prove

Workflow A (`tests/test_workflow_a_contracts.py`, plus the existing pipeline tests):
sibling subtitles are never opened or used; the transcript comes only from ASR; an
ASR failure propagates and writes nothing; a media error is typed; hallucinated
signature text never reaches translation; output is valid, ordered and QC'd; the
glossary reaches translation; a failed write leaves an existing output intact.
Audio-stream selection, language detection and caching are in
`test_pipeline_stream_selection`, `test_pipeline_language`, `test_pipeline_cache`;
segmentation and timing in `test_pipeline_fragmentation`,
`test_pipeline_span_distribution`, `test_segmentation_*`, `test_projection`.

Workflow B (`tests/test_workflow_b_pipeline.py`, `test_srt_translation.py`): ASR is
never invoked (a test patches it to fail, and another imports the module in a fresh
process and checks ASR is not loaded, `test_errors`); the source is parsed and
validated (malformed fails, WebVTT accepted); language is detected or validated;
translation happens one cue per span; every English cue stays inside its source
cue's time envelope with no offset; the original is preserved beside the English
file; the source is never modified; output is atomic.

## Determinism and models

Tests never load a model. ASR and translation are replaced by fakes at the
`pipeline.asr_transcribe` and `translate.translate_spans` seams, so they are exact
and fast (the whole backend suite runs in about 20 s). Behaviour that depends on a
model's output is measured separately with the scripts in `scripts/`
(`eval_transcription.py`, `eval_translation.py`, `eval_srt_quality.py`,
`compare_turn_detectors.py`) against real library data, and the numbers are
recorded in `benchmark-results/` and the decision records. A default is not changed
without such a measurement.

## Timing tests

`tests/srt_offset_fixtures.py` builds synthetic reference/candidate pairs with a
known offset to test the **evaluator**; they are not evidence about the pipeline.
Real pairs go in `tests/fixtures/timing/` (its README says how); until one exists
that test skips, visibly.

## Audit of the existing suite (2026-10-02)

- **Boundary tests were missing** for Workflow B's pipeline (fixed) and for
  "every route requires the key" (fixed: the `srt-translations` route was open).
- **Coupling to internals:** many tests patch `pipeline.asr_transcribe`,
  `pipeline.translate.translate_spans`, `worker_mod.pipeline.run` or module-level
  functions by name. They are fast but break on refactors that move those seams;
  the provider interfaces in `docs/architecture.md` section 14 would replace most
  of them with fakes.
- **No automated end-to-end or Docker test.** The container is exercised by hand.
- **No SSE contract test** beyond the events and API tests, and no browser-level
  frontend test (Playwright was proposed, not built).
- **Duplication:** not systematically measured; no duplicate suites were found in
  the files read. A full duplication audit is open (`docs/handover.md`).
- **Frontend coverage** is not measured.
