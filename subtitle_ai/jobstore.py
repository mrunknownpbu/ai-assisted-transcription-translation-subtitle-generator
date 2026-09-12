"""Durable job state. SQLite with `BEGIN IMMEDIATE` claims -- proven
race-safe design (retained because it's correct, verified by the tests
below, not because the prior implementation used it): two workers racing
to claim the same queued row cannot both succeed, because IMMEDIATE takes
the write lock before either can read-then-write.

Elapsed time semantics (tested explicitly, this is a previously-real bug
class): RUNNING/QUEUED tick against the live clock; every terminal status
freezes at finished_at forever after. A GUI polling once a second must
never see a COMPLETED job's timer still climbing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

STATUSES = ("queued", "running", "completed", "failed", "skipped", "cancelled")
TERMINAL_STATUSES = frozenset({"completed", "failed", "skipped", "cancelled"})
ACTIVE_STATUSES = frozenset({"queued", "running"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    video_path TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'QUEUED',
    progress REAL NOT NULL DEFAULT 0,
    source_lang TEXT,
    target_lang TEXT NOT NULL DEFAULT 'en',
    detected_language TEXT,
    language_confidence REAL,
    source_language_mode TEXT NOT NULL DEFAULT 'AUTO',
    requested_audio_stream INTEGER,
    selected_audio_stream INTEGER,
    embedded_stream_language TEXT,
    stream_selection_mode TEXT NOT NULL DEFAULT 'AUTO',
    selected_stream_reason TEXT,
    overwrite_original INTEGER NOT NULL DEFAULT 0,
    overwrite_english INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    updated_at REAL NOT NULL,
    error TEXT,
    error_category TEXT,
    outputs TEXT NOT NULL DEFAULT '[]',
    qc TEXT NOT NULL DEFAULT '{}',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    retry_of_job_id TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    log TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
"""


def _elapsed(row: dict) -> float:
    if not row.get("started_at"):
        return 0.0
    if row["status"] in ACTIVE_STATUSES:
        return time.time() - row["started_at"]
    end = row.get("finished_at") or row.get("updated_at") or row["started_at"]
    return end - row["started_at"]


class JobStoreError(Exception):
    pass


class JobStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """CREATE TABLE IF NOT EXISTS never adds columns to a table that
        already exists -- a jobs.db created before this schema version
        needs these added explicitly. Idempotent: skips any column
        already present, fresh databases included."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        migrations = {
            "target_lang": "ALTER TABLE jobs ADD COLUMN target_lang TEXT NOT NULL DEFAULT 'en'",
            "detected_language": "ALTER TABLE jobs ADD COLUMN detected_language TEXT",
            "language_confidence": "ALTER TABLE jobs ADD COLUMN language_confidence REAL",
            "source_language_mode":
                "ALTER TABLE jobs ADD COLUMN source_language_mode TEXT NOT NULL DEFAULT 'AUTO'",
            "requested_audio_stream": "ALTER TABLE jobs ADD COLUMN requested_audio_stream INTEGER",
            "selected_audio_stream": "ALTER TABLE jobs ADD COLUMN selected_audio_stream INTEGER",
            "embedded_stream_language": "ALTER TABLE jobs ADD COLUMN embedded_stream_language TEXT",
            "stream_selection_mode":
                "ALTER TABLE jobs ADD COLUMN stream_selection_mode TEXT NOT NULL DEFAULT 'AUTO'",
            "selected_stream_reason": "ALTER TABLE jobs ADD COLUMN selected_stream_reason TEXT",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                conn.execute(ddl)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _immediate(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["overwrite_original"] = bool(d["overwrite_original"])
        d["overwrite_english"] = bool(d["overwrite_english"])
        d["cancel_requested"] = bool(d["cancel_requested"])
        d["outputs"] = json.loads(d["outputs"])
        d["qc"] = json.loads(d["qc"])
        d["log"] = json.loads(d["log"])
        d["elapsed_seconds"] = _elapsed(d)
        return d

    def create(self, video_path: str, source_lang: str | None = "auto", *,
              target_lang: str = "en", audio_stream_index: int | None = None,
              overwrite_original: bool = False, overwrite_english: bool = False,
              retry_of_job_id: str | None = None, attempt: int = 1) -> dict:
        """Refuses to create a second QUEUED/RUNNING job for the same
        video -- a real gap an adversarial test caught: nothing previously
        stopped two concurrent API calls (or a double-click) from
        enqueueing the same video twice, wasting GPU time on a duplicate
        run. The existence check and the insert happen inside the same
        BEGIN IMMEDIATE transaction as claim() uses, for the same reason:
        two concurrent create() calls for the same video must not both
        pass the check before either commits.

        source_lang="auto" (the default) means "detect from the audio" --
        the job stores the *requested* language here; the actually
        detected language is a separate column (detected_language),
        populated once the pipeline determines it. Never conflate the two.
        Likewise audio_stream_index=None means "let the pipeline recommend
        a stream" (stream_selection_mode=AUTO); a given index is a manual
        override (stream_selection_mode=MANUAL) -- independent of whether
        the language itself is AUTO or MANUAL."""
        job_id = uuid.uuid4().hex
        now = time.time()
        source_lang = source_lang or "auto"
        source_language_mode = "MANUAL" if source_lang != "auto" else "AUTO"
        stream_selection_mode = "MANUAL" if audio_stream_index is not None else "AUTO"
        with self._immediate() as conn:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE video_path=? AND status IN ('queued','running')",
                (video_path,)).fetchone()
            if existing:
                raise JobStoreError(
                    f"an active job already exists for this video (id={existing['id']})")
            conn.execute(
                "INSERT INTO jobs (id, video_path, status, stage, progress, source_lang, target_lang, "
                "source_language_mode, requested_audio_stream, stream_selection_mode, "
                "overwrite_original, overwrite_english, created_at, updated_at, "
                "retry_of_job_id, attempt) "
                "VALUES (?, ?, 'queued', 'QUEUED', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, video_path, source_lang, target_lang, source_language_mode,
                 audio_stream_index, stream_selection_mode,
                 int(overwrite_original), int(overwrite_english),
                 now, now, retry_of_job_id, attempt))
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self, status: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        with self._connect() as conn:
            if status:
                total = conn.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (status,)).fetchone()[0]
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset)).fetchall()
            else:
                total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset)).fetchall()
        return [self._row_to_dict(r) for r in rows], total

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) as n FROM jobs GROUP BY status").fetchall()
        counts = {s.upper(): 0 for s in STATUSES}
        counts["ALL"] = 0
        for r in rows:
            counts[r["status"].upper()] = r["n"]
            counts["ALL"] += r["n"]
        return counts

    def claim(self) -> dict | None:
        """Race-safe: BEGIN IMMEDIATE takes the write lock before the
        SELECT, so two workers can never both claim the same row -- the
        second one blocks until the first's transaction commits, by
        which point the row is no longer 'queued'."""
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            now = time.time()
            conn.execute(
                "UPDATE jobs SET status='running', stage='RUNNING', started_at=?, updated_at=? WHERE id=?",
                (now, now, row["id"]))
        return self.get(row["id"])

    def update(self, job_id: str, **fields) -> None:
        if not fields:
            return
        fields = dict(fields)
        fields["updated_at"] = time.time()
        for key in ("outputs", "qc", "log"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        assignments = ", ".join(f"{k}=?" for k in fields)
        with self._immediate() as conn:
            conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", (*fields.values(), job_id))

    def append_log(self, job_id: str, message: str) -> None:
        job = self.get(job_id)
        if not job:
            return
        log = job["log"] + [{"time": time.time(), "message": message}]
        self.update(job_id, log=log)

    def finish(self, job_id: str, status: str, *, error: str | None = None,
              error_category: str | None = None, outputs: list | None = None,
              qc: dict | None = None) -> dict:
        if status not in TERMINAL_STATUSES:
            raise JobStoreError(f"finish() requires a terminal status, got {status!r}")
        now = time.time()
        fields = {"status": status, "stage": status.upper(), "progress": 100,
                  "finished_at": now, "error": error, "error_category": error_category}
        if outputs is not None:
            fields["outputs"] = outputs
        if qc is not None:
            fields["qc"] = qc
        self.update(job_id, **fields)
        return self.get(job_id)

    def request_cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        if not job:
            raise JobStoreError("job not found")
        if job["status"] in TERMINAL_STATUSES:
            raise JobStoreError(f"cannot cancel a terminal job (status={job['status']})")
        if job["status"] == "queued":
            # Never claimed -- cancel immediately, no worker to signal.
            return self.finish(job_id, "cancelled")
        self.update(job_id, cancel_requested=1)
        return self.get(job_id)

    def is_cancel_requested(self, job_id: str) -> bool:
        job = self.get(job_id)
        return bool(job and job["cancel_requested"])

    def retry(self, job_id: str, *, overwrite_original: bool = False,
             overwrite_english: bool = False, source_lang: str | None = None,
             audio_stream_index: int | None = "unset") -> dict:
        """Creates a NEW job row -- never mutates the original. The
        original's terminal timestamps/status remain exactly as they
        were; only the new row's `retry_of_job_id` links them.

        `source_lang`, if given, overrides the original's requested
        language for this retry -- e.g. supplying a manual language after
        an AUTO run failed on low-confidence detection. Omitted, it
        carries the original's requested language forward unchanged.
        `audio_stream_index` works the same way for stream selection; its
        default sentinel ("unset", not None) lets a caller explicitly
        pass None to force AUTO stream re-selection on retry, distinct
        from "didn't specify, carry the original forward"."""
        original = self.get(job_id)
        if not original:
            raise JobStoreError("job not found")
        if original["status"] not in TERMINAL_STATUSES:
            raise JobStoreError(f"cannot retry a non-terminal job (status={original['status']})")
        stream_override = (original.get("requested_audio_stream") if audio_stream_index == "unset"
                          else audio_stream_index)
        return self.create(original["video_path"], source_lang or original["source_lang"],
                           target_lang=original.get("target_lang") or "en",
                           audio_stream_index=stream_override,
                           overwrite_original=overwrite_original, overwrite_english=overwrite_english,
                           retry_of_job_id=job_id, attempt=original["attempt"] + 1)
