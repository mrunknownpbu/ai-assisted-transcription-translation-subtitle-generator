# CLAUDE.md

Standing rules for coding agents. Everything else is in `docs/`; read it there.

## Read first, in this order

1. `docs/product-requirements.md` -- what the product is and must do.
2. `docs/architecture.md` -- components, boundaries, invariants, error model.
3. `docs/agent-guide.md` -- how to change this repo (protocol, tests, docs).
4. `docs/handover.md` -- current state, open issues, next tasks.
5. `docs/decisions/README.md` -- dated decisions and measurements. A reference
   like `CLAUDE.md ("<title>")` in code or docs means the entry with that title
   there.

Also: `docs/api.md`, `docs/testing.md`, `docs/deployment.md`,
`docs/troubleshooting.md`, `docs/design-system.md`, `docs/operations.md`.

## Invariants (do not break; each has a decision record or test)

- **Audio is the only source of truth for Workflow A** (video to English). No
  subtitle text -- existing subtitle, embedded track, filename -- may become
  transcription input, influence ASR, or repair ASR output. Workflow B (existing
  subtitle to English) never runs ASR. Never mix them.
  (`docs/decisions/2026-10-02-audio-only-asr-inputs.md`)
- **Output writes are atomic** (temp file, validate, rename). A known-good
  subtitle is never partially overwritten; a failed job leaves it intact.
- **Never add a magic timing offset.** Timing problems are diagnosed with
  `scripts/eval_srt_quality.py`, then fixed at the cause.
  (`docs/decisions/subtitle-timing.md`)
- **The API is not exposed unauthenticated beyond loopback**: the server refuses
  to start without `SUBTITLE_AI_API_KEY` on a non-loopback address unless
  `SUBTITLE_AI_ALLOW_INSECURE` is set. Every `/api/*` route except health needs
  the key; a test enforces it.
- **QC is advisory**, not a completion gate. Only structural invalidity fails a
  job. (`docs/decisions/qc-is-advisory-not-a-gate.md`)
- **Names are protected only on evidence** (credited by cast metadata, recurring
  in the transcript, and a demonstrated mistranslation). Mined candidates are
  suggestions. (`docs/decisions/entity-protection-evidence-bar.md`)
- **GPU access** goes through the shared lock and VRAM pre-flight (`gpu.py`).
- **Stored job `status` values and `error_category` codes are a public
  contract**: add, never rename. (`subtitle_ai/errors.py`, `jobstore.lifecycle`)
- **Do not weaken or delete a test to make a change pass.** Do not add a
  constant without evidence. Do not change product behaviour silently.

## Commands

```bash
# Backend (every test is unittest-style; pytest also works)
cd subtitle_ai && PYTHONPATH=.:../tests ../.venv/bin/python -m unittest discover -s ../tests -t ../tests
uvx ruff@0.16.10 check subtitle_ai scripts tests
uvx mypy@2.4.0 --python-executable .venv/bin/python     # no exclusions

# Frontend
cd frontend && npx tsc --noEmit && npm test && npm run build

# Deploy (builds subtitle-ai:dev, recreates the container)
./scripts/deploy.sh
curl http://localhost:8099/api/health
```

CI (`.github/workflows/test.yml`) runs ruff, mypy, backend tests, frontend
typecheck/tests/build. Keep it green; do not exclude files to make it pass.

## Rules for this file

Keep it short: invariants, commands, pointers. Narrative, measurements and
history go in `docs/decisions/` (one file per decision, plus a row in its README).
