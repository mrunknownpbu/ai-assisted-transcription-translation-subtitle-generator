"""Job failure alerting: an optional webhook fired when a job's status
becomes "failed", read from FAILURE_WEBHOOK_URL. Off by default.

Real gap this closes (production-readiness audit, 2026-09-21): a job
failure was previously invisible until someone opened the UI -- no
webhook, email, or chat integration existed anywhere. Deliberately a
generic JSON POST, not a per-service integration (Slack/Discord/ntfy.sh/
a Telegram bot relay all accept a plain POST, or can be fronted by
something that does) -- no new dependency beyond httpx, already used by
translate.py/tvdb_client.py.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 10.0


def notify_job_failed(job: dict) -> None:
    """Best-effort only, by design: called AFTER the job's terminal state
    is already committed to the job store, so a webhook outage or
    misconfiguration must never affect the job itself -- any failure
    here is caught and logged, never re-raised."""
    url = os.environ.get("FAILURE_WEBHOOK_URL")
    if not url:
        return
    payload = {
        "job_id": job.get("id"),
        "job_type": job.get("job_type"),
        "video_path": job.get("video_path"),
        "source_srt_path": job.get("source_srt_path"),
        "error": job.get("error"),
        "error_category": job.get("error_category"),
    }
    try:
        httpx.post(url, json=payload, timeout=WEBHOOK_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("failure webhook POST to %s failed: %s", url, exc)
