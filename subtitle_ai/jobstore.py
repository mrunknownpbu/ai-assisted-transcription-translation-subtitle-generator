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

import glossary_profile

STATUSES = ("queued", "running", "completed", "failed", "skipped", "cancelled")
TERMINAL_STATUSES = frozenset({"completed", "failed", "skipped", "cancelled"})
ACTIVE_STATUSES = frozenset({"queued", "running"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL DEFAULT 'video',
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
    log TEXT NOT NULL DEFAULT '[]',
    tvdb_id INTEGER,
    source_srt_path TEXT,
    destination_srt_path TEXT,
    source_is_uploaded INTEGER NOT NULL DEFAULT 0
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
    def __init__(self, db_path: str | Path, *, on_change=None):
        # on_change(job_id: str), if given, fires after every commit that
        # mutates a job row (create/update/claim -- finish/append_log/
        # request_cancel/retry all funnel through create()/update(), so a
        # hook in exactly those two plus claim() covers every mutation
        # path with no call site able to forget it). Kept as a plain
        # optional callable, not an import of events.EventBus, so this
        # module stays decoupled from the GUI push mechanism entirely --
        # main.py wires the real EventBus.publish in.
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._on_change = on_change
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
            "tvdb_id": "ALTER TABLE jobs ADD COLUMN tvdb_id INTEGER",
            # 'video' default backfills every pre-existing row as a video
            # job -- exactly what they all were before job_type existed.
            "job_type": "ALTER TABLE jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'video'",
            "source_srt_path": "ALTER TABLE jobs ADD COLUMN source_srt_path TEXT",
            "destination_srt_path": "ALTER TABLE jobs ADD COLUMN destination_srt_path TEXT",
            "source_is_uploaded":
                "ALTER TABLE jobs ADD COLUMN source_is_uploaded INTEGER NOT NULL DEFAULT 0",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                conn.execute(ddl)
        # Deliberately NOT gated on "column just added": a real deploy
        # sequence caught this the hard way -- the column can already
        # exist (added by an earlier release) with rows still NULL
        # because the backfill logic itself shipped later. Re-deriving
        # via the same pure find_tvdb_id(video_path) create() uses is
        # cheap (a regex match) and idempotent (a legitimately-untagged
        # row stays NULL every time), so it's safe to run on every
        # startup rather than try to gate a one-time migration correctly.
        for row in conn.execute("SELECT id, video_path FROM jobs WHERE tvdb_id IS NULL").fetchall():
            tvdb_id = glossary_profile.find_tvdb_id(row["video_path"])
            if tvdb_id is not None:
                conn.execute("UPDATE jobs SET tvdb_id=? WHERE id=?", (tvdb_id, row["id"]))

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

    def _notify(self, job_id: str) -> None:
        if self._on_change:
            self._on_change(job_id)

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["overwrite_original"] = bool(d["overwrite_original"])
        d["overwrite_english"] = bool(d["overwrite_english"])
        d["cancel_requested"] = bool(d["cancel_requested"])
        d["source_is_uploaded"] = bool(d["source_is_uploaded"])
        d["outputs"] = json.loads(d["outputs"])
        d["qc"] = json.loads(d["qc"])
        d["log"] = json.loads(d["log"])
        d["elapsed_seconds"] = _elapsed(d)
        return d

    def _insert_job(self, conn: sqlite3.Connection, *, job_id: str, job_type: str,
                    video_path: str, source_lang: str, target_lang: str,
                    source_language_mode: str, requested_audio_stream: int | None,
                    stream_selection_mode: str, overwrite_original: bool, overwrite_english: bool,
                    created_at: float, updated_at: float, retry_of_job_id: str | None,
                    attempt: int, tvdb_id: int | None, source_srt_path: str | None = None,
                    destination_srt_path: str | None = None,
                    source_is_uploaded: bool = False) -> None:
        """The one INSERT statement for the jobs table -- shared by
        create() and create_srt_translation() so the two job types never
        drift into two subtly-different SQL statements for the same
        table. Must run inside the caller's own BEGIN IMMEDIATE block
        (see create()/create_srt_translation()), not its own -- the
        duplicate-active-job check and the insert have to be atomic
        together, and this helper doesn't own that transaction."""
        conn.execute(
            "INSERT INTO jobs (id, job_type, video_path, status, stage, progress, source_lang, "
            "target_lang, source_language_mode, requested_audio_stream, stream_selection_mode, "
            "overwrite_original, overwrite_english, created_at, updated_at, retry_of_job_id, "
            "attempt, tvdb_id, source_srt_path, destination_srt_path, source_is_uploaded) "
            "VALUES (?, ?, ?, 'queued', 'QUEUED', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, job_type, video_path, source_lang, target_lang, source_language_mode,
             requested_audio_stream, stream_selection_mode, int(overwrite_original),
             int(overwrite_english), created_at, updated_at, retry_of_job_id, attempt, tvdb_id,
             source_srt_path, destination_srt_path, int(source_is_uploaded)))

    def create(self, video_path: str, source_lang: str | None = "auto", *,
              target_lang: str = "en", audio_stream_index: int | None = None,
              overwrite_original: bool = False, overwrite_english: bool = False,
              retry_of_job_id: str | None = None, attempt: int = 1) -> dict:
        """Refuses to create a second QUEUED/RUNNING VIDEO job for the same
        video -- a real gap an adversarial test caught: nothing previously
        stopped two concurrent API calls (or a double-click) from
        enqueueing the same video twice, wasting GPU time on a duplicate
        run. The existence check and the insert happen inside the same
        BEGIN IMMEDIATE transaction as claim() uses, for the same reason:
        two concurrent create() calls for the same video must not both
        pass the check before either commits. Scoped to job_type='video'
        so it never collides with an unrelated srt_translation job that
        happens to reference the same episode (see create_srt_translation(),
        which has its own, separately-scoped duplicate check).

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
        tvdb_id = glossary_profile.find_tvdb_id(video_path)
        with self._immediate() as conn:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE job_type='video' AND video_path=? "
                "AND status IN ('queued','running')", (video_path,)).fetchone()
            if existing:
                raise JobStoreError(
                    f"an active job already exists for this video (id={existing['id']})")
            self._insert_job(conn, job_id=job_id, job_type="video", video_path=video_path,
                             source_lang=source_lang, target_lang=target_lang,
                             source_language_mode=source_language_mode,
                             requested_audio_stream=audio_stream_index,
                             stream_selection_mode=stream_selection_mode,
                             overwrite_original=overwrite_original, overwrite_english=overwrite_english,
                             created_at=now, updated_at=now, retry_of_job_id=retry_of_job_id,
                             attempt=attempt, tvdb_id=tvdb_id)
        self._notify(job_id)
        return self.get(job_id)

    def create_srt_translation(self, source_srt_path: str, destination_srt_path: str, *,
                               source_lang: str | None = "auto", target_lang: str = "en",
                               video_path: str | None = None, overwrite_english: bool = False,
                               source_is_uploaded: bool = False,
                               retry_of_job_id: str | None = None, attempt: int = 1) -> dict:
        """A job that translates an already-transcribed original-language
        .srt straight to English -- no ASR, no audio, no video processing.
        `video_path`, if given, is only the ASSOCIATED episode (for series
        grouping / tvdb_id lookup / display on the Series pages), never
        the file this job actually processes -- that's source_srt_path.
        Stored as '' (never NULL, since the column is NOT NULL) when no
        association is given, matching how AUTO/None is represented
        elsewhere in this table rather than introducing a second way to
        say "no value".

        Deliberately reuses target_lang/overwrite_english/tvdb_id exactly
        as video jobs do; overwrite_original and every audio-stream field
        stay at their schema defaults, unused, since there is no
        "original" audio track being kept or replaced here -- only ever
        one output file, destination_srt_path.

        Duplicate-active-job check is scoped to job_type='srt_translation'
        and keyed on destination_srt_path (not video_path, which two
        different source subtitles could legitimately share): two jobs
        must never race to write the same destination concurrently."""
        job_id = uuid.uuid4().hex
        now = time.time()
        source_lang = source_lang or "auto"
        source_language_mode = "MANUAL" if source_lang != "auto" else "AUTO"
        tvdb_id = glossary_profile.find_tvdb_id(video_path) if video_path else None
        with self._immediate() as conn:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE job_type='srt_translation' AND destination_srt_path=? "
                "AND status IN ('queued','running')", (destination_srt_path,)).fetchone()
            if existing:
                raise JobStoreError(
                    "an active SRT-translation job already exists for this destination "
                    f"(id={existing['id']})")
            self._insert_job(conn, job_id=job_id, job_type="srt_translation",
                             video_path=video_path or "", source_lang=source_lang,
                             target_lang=target_lang, source_language_mode=source_language_mode,
                             requested_audio_stream=None, stream_selection_mode="AUTO",
                             overwrite_original=False, overwrite_english=overwrite_english,
                             created_at=now, updated_at=now, retry_of_job_id=retry_of_job_id,
                             attempt=attempt, tvdb_id=tvdb_id, source_srt_path=source_srt_path,
                             destination_srt_path=destination_srt_path,
                             source_is_uploaded=source_is_uploaded)
        self._notify(job_id)
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

    def list_series(self) -> list[dict]:
        """One summary row per distinct tvdb_id, plus a single row for
        `tvdb_id IS NULL` (jobs whose video_path carries no `{tvdb-<id>}`
        tag) -- every job lands in exactly one bucket, mirroring
        counts()'s ALL semantics but grouped by series instead of by
        status."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT tvdb_id, status, COUNT(*) as n, MAX(updated_at) as last_updated "
                "FROM jobs GROUP BY tvdb_id, status").fetchall()
        series: dict[int | None, dict] = {}
        for r in rows:
            key = r["tvdb_id"]
            entry = series.setdefault(key, {"tvdb_id": key, "counts": {s.upper(): 0 for s in STATUSES},
                                            "total": 0, "last_updated": 0.0})
            entry["counts"][r["status"].upper()] = r["n"]
            entry["total"] += r["n"]
            entry["last_updated"] = max(entry["last_updated"], r["last_updated"] or 0.0)
        return sorted(series.values(), key=lambda e: e["last_updated"], reverse=True)

    def list_by_tvdb_id(self, tvdb_id: int | None) -> list[dict]:
        # video_path sorts naturally today because every real filename in
        # this deployment's library is zero-padded (S01E01, S01E02, ...,
        # S01E10) -- see media naming convention throughout this project.
        with self._connect() as conn:
            if tvdb_id is None:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE tvdb_id IS NULL ORDER BY video_path").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE tvdb_id=? ORDER BY video_path", (tvdb_id,)).fetchall()
        return [self._row_to_dict(r) for r in rows]

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
        self._notify(row["id"])
        return self.get(row["id"])

    def recover_orphaned_jobs(self) -> list[str]:
        """Call once at startup, after _migrate() and before the worker
        starts claiming. Real gap this closes (2026-09-19 audit): a
        `running` row means "a worker thread in SOME process is actively
        inside pipeline.run() for this job" -- that invariant silently
        breaks the moment the process holding that thread dies (crash,
        OOM-kill, redeploy) without ever reaching worker.py's `finally`/
        `except` handling, since claim() only ever selects
        status='queued' rows. Without this, such a job's row is stuck
        `running` forever: never reclaimed, its elapsed-time display
        climbing indefinitely, invisible to any log or alert.

        Resetting straight back to 'queued' (not a new terminal
        'interrupted' status) is safe specifically because this process
        is single-worker/single-thread (see worker.py's module
        docstring): by the time this method runs, at startup, no
        pipeline.run() call from a PRIOR process instance can still be
        executing -- there is no live worker to race against a row this
        call touches. A currently-running job in a live process is never
        affected: this only ever runs once, at construction-adjacent
        startup, before Worker.start() is ever called (see main.py)."""
        with self._immediate() as conn:
            rows = conn.execute("SELECT id FROM jobs WHERE status='running'").fetchall()
            if rows:
                now = time.time()
                conn.execute(
                    "UPDATE jobs SET status='queued', stage='QUEUED', started_at=NULL, "
                    "updated_at=? WHERE status='running'", (now,))
        job_ids = [r["id"] for r in rows]
        for job_id in job_ids:
            self._notify(job_id)
        return job_ids

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
        self._notify(job_id)

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

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job:
            raise JobStoreError("job not found")
        if job["status"] not in TERMINAL_STATUSES:
            raise JobStoreError(f"cannot delete an active job (status={job['status']})")
        with self._immediate() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        self._notify(job_id)

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

    def retry(self, job_id: str, *, overwrite_original: bool | str = "unset",
             overwrite_english: bool | str = "unset", source_lang: str | None = None,
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
        from "didn't specify, carry the original forward".

        `overwrite_original`/`overwrite_english` use the identical
        "unset" sentinel: real bug this fixes (2026-09-19) -- a plain
        `bool = False` default here meant every retry submitted with an
        empty request body (the GUI's one-click Retry button does exactly
        this) silently reset both flags to False, so a retry of a job
        that had already written real output would KEEP those stale
        files instead of replacing them, with no error or indication.
        Omitted now means "inherit the original job's own flag";
        explicit True/False always overrides it, same as source_lang.

        `job_type` is never a caller-supplied parameter here -- a retry
        always stays the same job type as the job it retries, read off
        `original` itself, so an srt_translation retry can never
        accidentally become a video job or vice versa."""
        original = self.get(job_id)
        if not original:
            raise JobStoreError("job not found")
        if original["status"] not in TERMINAL_STATUSES:
            raise JobStoreError(f"cannot retry a non-terminal job (status={original['status']})")
        overwrite_original = (original["overwrite_original"] if overwrite_original == "unset"
                              else overwrite_original)
        overwrite_english = (original["overwrite_english"] if overwrite_english == "unset"
                             else overwrite_english)
        if original["job_type"] == "srt_translation":
            return self.create_srt_translation(
                original["source_srt_path"], original["destination_srt_path"],
                source_lang=source_lang or original["source_lang"],
                target_lang=original.get("target_lang") or "en",
                video_path=original["video_path"] or None, overwrite_english=overwrite_english,
                source_is_uploaded=original["source_is_uploaded"],
                retry_of_job_id=job_id, attempt=original["attempt"] + 1)
        stream_override = (original.get("requested_audio_stream") if audio_stream_index == "unset"
                          else audio_stream_index)
        return self.create(original["video_path"], source_lang or original["source_lang"],
                           target_lang=original.get("target_lang") or "en",
                           audio_stream_index=stream_override,
                           overwrite_original=overwrite_original, overwrite_english=overwrite_english,
                           retry_of_job_id=job_id, attempt=original["attempt"] + 1)
