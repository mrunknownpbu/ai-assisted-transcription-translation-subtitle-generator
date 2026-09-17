"""Process entrypoint: wires JobStore + Worker + API together. Run with
`uvicorn main:app`.
"""

from __future__ import annotations

import os

import api
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

# Glossary is loaded PER JOB, keyed by that job's own tvdb_id -- see
# Worker._load_glossary_entities(). A one-time load here (as this used to
# do, with no tvdb_id) could only ever see the global/category layer; a
# series-specific file would never be selected regardless of its content.
_worker = Worker(api.get_store(), MEDIA_ROOT, WORK_ROOT, glossary_dir=GLOSSARY_DIR,
                transcript_cache_dir=TRANSCRIPT_CACHE_DIR)
_worker.start()
