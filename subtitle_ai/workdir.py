"""Lifecycle of the per-job scratch directories under WORK_ROOT.

`WORK_ROOT/<job_id>` holds transient artifacts only -- the extracted WAV
(the large one), stream-sample clips, and the pre-commit source/target
SRTs. worker.py copies the final SRTs into the media library via
output.write_srt_atomic() BEFORE marking a job completed, so nothing in
here is needed once a job succeeds. It was never removed, so it grew
without bound.

Policy (see Worker._finalize_work_dir and sweep_stale):
  completed / cancelled / skipped -> removed as soon as the job is terminal
  failed (incl. validation failure) -> kept for a bounded diagnostic
                                       window, then removed by the sweep
  job deleted via the API          -> removed immediately

Deliberately no project imports beyond jobstore's status constants, and
no torch: this must stay cheap and safe to import from api.py.

Everything here is best-effort and never raises: a cleanup problem must
not change an already-terminal job's result, so failures are logged.
Only a DIRECT child of WORK_ROOT is ever removed -- never a symlink, and
never a path outside it -- so nothing else that lives under /cache
(transcript cache, job database, SRT upload staging) can be touched.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

from jobstore import ACTIVE_STATUSES

logger = logging.getLogger(__name__)

DEFAULT_FAILED_RETENTION_HOURS = 24.0

# Job ids are uuid4().hex (see jobstore.py); the pattern is looser only
# so a legacy/foreign id still works, but it excludes every character
# that could make `work_root / job_id` resolve anywhere else.
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def parse_retention_hours(raw: str | None, default: float = DEFAULT_FAILED_RETENTION_HOURS) -> float:
    """SUBTITLE_AI_FAILED_WORK_RETENTION_HOURS. Unset or empty -> default;
    an unparseable or negative value also falls back to the default (with
    a warning) rather than failing startup over a scratch-disk setting."""
    if raw is None or not raw.strip():
        return default
    try:
        hours = float(raw)
    except ValueError:
        hours = -1.0
    if hours < 0 or hours != hours:  # negative, or NaN
        logger.warning("ignoring invalid SUBTITLE_AI_FAILED_WORK_RETENTION_HOURS=%r; using %sh",
                       raw, default)
        return default
    return hours


def cleanup_work_dir(work_root: str | Path, job_id: str) -> bool:
    """Removes `<work_root>/<job_id>`. Returns whether a directory was
    actually removed; never raises."""
    if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
        logger.warning("refusing to clean work dir for malformed job id %r", job_id)
        return False
    root = Path(work_root)
    target = root / job_id
    try:
        # lstat-based checks first: a symlink named like a job id must be
        # refused outright, not followed -- rmtree would refuse it anyway,
        # but the explicit check keeps the reason in the log.
        if target.is_symlink():
            logger.warning("refusing to clean work dir %s: it is a symlink", target)
            return False
        if not target.is_dir():
            return False
        if target.resolve().parent != root.resolve():
            logger.warning("refusing to clean %s: not directly beneath work root %s", target, root)
            return False
        shutil.rmtree(target)
        return True
    except OSError:
        logger.warning("could not clean work dir %s", target, exc_info=True)
        return False


def sweep_stale(work_root: str | Path, store, retention_hours: float,
                now: float | None = None) -> list[str]:
    """One pass over every direct child directory of WORK_ROOT, consulting
    the job store so a directory is only removed when that is actually
    safe. Returns the removed job ids.

      queued/running job      -> never touched
      completed/cancelled/... -> removed (normally already gone; this
                                 catches a crash between finish and cleanup)
      failed job              -> removed once `finished_at` is older than
                                 the retention window
      no job row (orphan)     -> removed once the directory's mtime is
                                 older than the window
    """
    root = Path(work_root)
    if not root.is_dir():
        return []
    now = time.time() if now is None else now
    cutoff = now - retention_hours * 3600.0
    removed: list[str] = []
    for child in sorted(root.iterdir()):
        try:
            if child.is_symlink() or not child.is_dir():
                continue
            job = store.get(child.name)
            if job is None:
                stale = child.stat().st_mtime <= cutoff
            elif job["status"] in ACTIVE_STATUSES:
                stale = False
            elif job["status"] == "failed":
                finished = job.get("finished_at") or child.stat().st_mtime
                stale = finished <= cutoff
            else:
                stale = True
            if stale and cleanup_work_dir(root, child.name):
                removed.append(child.name)
        except Exception:  # noqa: BLE001 -- one bad entry must not stop the sweep
            logger.warning("work-dir sweep skipped %s", child, exc_info=True)
    if removed:
        logger.info("work-dir sweep removed %d stale director%s",
                    len(removed), "y" if len(removed) == 1 else "ies")
    return removed
