"""Process entrypoint: wires JobStore + Worker + API together. Run with
`uvicorn main:app`.
"""

from __future__ import annotations

import logging
import os

import api
from worker import Worker

# Real gap this closes (production-readiness audit, 2026-09-21): zero
# use of the logging module existed anywhere in subtitle_ai/*.py -- the
# only durable record of anything was the per-job `log` JSON column,
# which says nothing about process-level events (startup, worker
# liveness, EventBus/uvicorn issues). Plain stdout text, one line per
# record: matches Docker's default json-file log driver either way (it
# wraps whatever a container writes to stdout), and this project has no
# existing log-aggregation stack to format for specifically -- adding a
# JSON formatter now would be speculative complexity with nothing to
# consume it yet.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DB_PATH = os.environ.get("SUBTITLE_AI_DB", "/cache/jobs.db")
MEDIA_ROOT = os.environ.get("SUBTITLE_AI_MEDIA_ROOT", "/data")
WORK_ROOT = os.environ.get("SUBTITLE_AI_WORK_ROOT", "/cache/work")
GLOSSARY_DIR = os.environ.get("SUBTITLE_AI_GLOSSARY_DIR", "/glossary")
# Persistent, not per-job like WORK_ROOT/<job_id> -- must survive across
# jobs (that's the entire point) under the same /cache mount already used
# for the job database and scratch work, so no new volume is needed.
TRANSCRIPT_CACHE_DIR = os.environ.get("SUBTITLE_AI_TRANSCRIPT_CACHE", "/cache/transcripts")
# Human-readable review output only -- never read back by load_profile()
# or any pipeline code (see auto_glossary.write_suggestions()). Must be
# writable, unlike GLOSSARY_DIR (mounted read-only in production -- see
# compose.yml), so it lives under the same /cache mount as the job DB
# and transcript cache, not under GLOSSARY_DIR.
GLOSSARY_SUGGESTIONS_DIR = os.environ.get(
    "SUBTITLE_AI_GLOSSARY_SUGGESTIONS_DIR", "/cache/glossary_suggestions")
# Staging area for real browser-uploaded SRT files -- deliberately under
# /cache (app-owned state), never /data (the real media library): an
# uploaded file is transient input, not something that belongs in the
# user's actual library.
SRT_UPLOAD_DIR = os.environ.get("SUBTITLE_AI_SRT_UPLOAD_DIR", "/cache/srt_uploads")
# Unset by default = today's exact behavior (local Tesla P4 translation
# only). See translate.remote_translate_batch()'s docstring for the real
# benchmark (~8x throughput) motivating this, and Worker's docstring for
# the automatic local fallback if the remote server is unreachable.
TRANSLATE_SERVER_URL = os.environ.get("TRANSLATE_SERVER_URL")

app = api.create_app(DB_PATH, MEDIA_ROOT, glossary_dir=GLOSSARY_DIR,
                     glossary_suggestions_dir=GLOSSARY_SUGGESTIONS_DIR,
                     srt_upload_dir=SRT_UPLOAD_DIR)

# Recover any job left 'running' by a prior process instance (crash,
# OOM-kill, redeploy) BEFORE the worker starts claiming -- see
# JobStore.recover_orphaned_jobs()'s docstring for why this ordering is
# what makes resetting straight to 'queued' safe.
api.get_store().recover_orphaned_jobs()

# Glossary is loaded PER JOB, keyed by that job's own tvdb_id -- see
# Worker._load_glossary_profile(). A one-time load here (as this used to
# do, with no tvdb_id) could only ever see the global/category layer; a
# series-specific file would never be selected regardless of its content.
_worker = Worker(api.get_store(), MEDIA_ROOT, WORK_ROOT, glossary_dir=GLOSSARY_DIR,
                transcript_cache_dir=TRANSCRIPT_CACHE_DIR,
                glossary_suggestions_dir=GLOSSARY_SUGGESTIONS_DIR,
                srt_upload_dir=SRT_UPLOAD_DIR,
                translate_server_url=TRANSLATE_SERVER_URL)
api.register_worker(_worker)
_worker.start()
