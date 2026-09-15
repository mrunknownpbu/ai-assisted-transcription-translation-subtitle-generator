# SubtitleAI — Self-Hosted AI Subtitle Platform

Processes media files to detect dialogue audio, transcribe it with word-level timing,
translate it into any number of target languages, and generate precisely-timed
multilingual subtitles — with the actual audio waveform as the only source of truth at
every stage. A second, clearly-separate job type also translates a subtitle file you
already have directly (no audio, no ASR) — see `docs/ARCHITECTURE.md`'s "Direct subtitle
translation" section.

- **Architecture**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the 11-stage pipeline,
  how each stage works, and why.
- **Deployment**: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Docker Compose setup for
  CPU-only, NVIDIA, and AMD ROCm hosts.
- **Runbook**: [docs/RUNBOOK.md](docs/RUNBOOK.md) — day-2 operations, troubleshooting,
  backups.

## Quick start

```bash
cp .env.example .env
docker compose up --build -d        # CPU-only, works everywhere
# or: docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up --build -d
```

Open http://localhost:8080.

## Repository layout

```
backend/    FastAPI app, the 11 pipeline stages, job queue/worker, SQLite models, tests
frontend/   React + TypeScript + Vite GUI
docs/       Architecture, deployment, and runbook documentation
docker-compose*.yml   Base (CPU) stack + NVIDIA/ROCm overrides
```

## Running the backend test suite

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu   # see requirements.txt note
PYTHONPATH=. pytest -m unit                          # fast, no model downloads
PYTHONPATH=. pytest -m concurrency                   # queue/GPU-semaphore race tests
PYTHONPATH=. pytest -m integration                   # real ASR/translation/VAD, downloads models on first run
```

## Key design decisions

- **ASR**: `faster-whisper` primary (`large-v3` by default), with a configurable fallback
  chain (`openai-whisper` → `whisper.cpp` → OpenAI Whisper API → `vosk`) so a job degrades
  gracefully instead of hard-failing when one engine is unavailable.
- **Translation**: local NLLB-200 primary (`distilled-1.3B` by default — self-hosted, ~200
  languages, no API key), behind the same pluggable fallback pattern as ASR, with beam
  search + n-gram blocking to prevent degenerate repetition loops.
- **Entity protection**: an optional glossary (manually supplied, or auto-populated from a
  TVDB series' cast) protects names from mistranslation via opaque placeholder
  substitution during translation.
- **Hallucination defense**: a weighted evidence score (VAD, confidence, compression
  ratio, known-artifact signatures, repetition analysis, cross-segment recurrence) tuned to
  minimize false negatives — ambiguous segments are kept, not dropped, and every decision
  is logged with its score, reason, and threshold.
- **QC**: four independent passes (cue timing/readability, translation content, entity
  reconciliation, final output-file re-validation) — every check logs pass or fail, not
  just failures.
- **Timing**: derived only from real ASR word timestamps (or, for direct subtitle
  translation, taken verbatim from the input file) — never a global shift or external
  resync.
- **Concurrency**: SQLite in WAL mode with atomic claim/GPU-semaphore SQL, verified under
  real concurrent-thread races in `backend/tests/concurrency/`.
