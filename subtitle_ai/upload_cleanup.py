"""Retention of transient, browser-uploaded SRT sources under /cache."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SRT_UPLOAD_RETENTION_HOURS = 7 * 24
_UPLOAD_NAME_RE = re.compile(r"^[0-9a-f]{32}\.srt$")


def sweep_stale(upload_root: str | Path, store, *,
                retention_hours: float = SRT_UPLOAD_RETENTION_HOURS,
                now: float | None = None) -> list[str]:
    """Remove expired upload files unless a queued/running job still uses them."""
    root = Path(upload_root)
    if not root.is_dir():
        return []
    now = time.time() if now is None else now
    cutoff = now - retention_hours * 3600.0
    active_sources = store.active_uploaded_sources()
    removed: list[str] = []
    for child in sorted(root.iterdir()):
        try:
            if (child.is_symlink() or not child.is_file()
                    or not _UPLOAD_NAME_RE.fullmatch(child.name)
                    or child.name in active_sources):
                continue
            if child.resolve().parent != root.resolve() or child.stat().st_mtime > cutoff:
                continue
            child.unlink()
            removed.append(child.name)
        except Exception:
            logger.warning("SRT upload sweep skipped %s", child, exc_info=True)
    if removed:
        logger.info("SRT upload sweep removed %d expired file%s",
                    len(removed), "" if len(removed) == 1 else "s")
    return removed
