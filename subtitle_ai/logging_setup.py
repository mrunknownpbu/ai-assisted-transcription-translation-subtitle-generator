"""Process logging: one line per record, tagged with the job being processed.

`job_context(job_id)` binds a job id for the current thread of execution, and
every record logged inside it carries that id -- so one grep (or one JSON
field) follows a job through the worker, pipeline, ASR and translation
without each call site having to repeat it.

`SUBTITLE_AI_LOG_FORMAT=json` switches the output to one JSON object per line
(`ts`, `level`, `logger`, `message`, plus `job_id` and `exc` when present) for
a log aggregator; the default stays plain text. Only this app's own records
go through here -- uvicorn writes its access/error logs with its own
formatters.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import sys
from collections.abc import Iterator, Mapping

_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("subtitle_ai_job_id", default=None)

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s%(job_suffix)s"


@contextlib.contextmanager
def job_context(job_id: str | None) -> Iterator[None]:
    token = _job_id.set(job_id)
    try:
        yield
    finally:
        _job_id.reset(token)


class JobContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        job_id = _job_id.get()
        record.job_id = job_id
        record.job_suffix = f" [job={job_id}]" if job_id else ""
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        job_id = getattr(record, "job_id", None)
        if job_id:
            payload["job_id"] = job_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure(env_override: Mapping[str, str] | None = None, stream=None) -> None:
    """Install the root handler (replacing any existing ones)."""
    env: Mapping[str, str] = os.environ if env_override is None else env_override
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(JobContextFilter())
    if env.get("SUBTITLE_AI_LOG_FORMAT", "").strip().lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))
    level = env.get("SUBTITLE_AI_LOG_LEVEL", "INFO").strip().upper()
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level if level in logging.getLevelNamesMapping() else "INFO")
