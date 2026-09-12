"""Process entrypoint: wires JobStore + Worker + API together. Run with
`uvicorn main:app`.
"""

from __future__ import annotations

import os

import api
import glossary_profile
from worker import Worker

DB_PATH = os.environ.get("SUBTITLE_AI_DB", "/cache/jobs.db")
MEDIA_ROOT = os.environ.get("SUBTITLE_AI_MEDIA_ROOT", "/data")
WORK_ROOT = os.environ.get("SUBTITLE_AI_WORK_ROOT", "/cache/work")
GLOSSARY_DIR = os.environ.get("SUBTITLE_AI_GLOSSARY_DIR", "/glossary")
# Persistent, not per-job like WORK_ROOT/<job_id> -- must survive across
# jobs (that's the entire point) under the same /cache mount already used
# for the job database and scratch work, so no new volume is needed.
TRANSCRIPT_CACHE_DIR = os.environ.get("SUBTITLE_AI_TRANSCRIPT_CACHE", "/cache/transcripts")

app = api.create_app(DB_PATH, MEDIA_ROOT)

try:
    _profile = glossary_profile.load_profile(GLOSSARY_DIR)
    _entities = _profile.entities
except FileNotFoundError:
    _entities = []

_worker = Worker(api.get_store(), MEDIA_ROOT, WORK_ROOT, glossary_entities=_entities,
                transcript_cache_dir=TRANSCRIPT_CACHE_DIR)
_worker.start()
