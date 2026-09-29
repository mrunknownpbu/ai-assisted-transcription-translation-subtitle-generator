"""Reading, writing and versioning the series glossary YAML files that the
web UI edits (api.py's promote/update/delete endpoints).

Two real gaps this closes (found 2026-09-28, in the live glossary repo):

1. The endpoints wrote with pyyaml's safe_dump(), which drops every
   comment. One UI promotion on 2026-09-22 silently deleted ~50 lines of
   evidence from love-is-in-the-air.yaml -- the notes recording *why* each
   name is protected and why others were deliberately left out (see
   CLAUDE.md's "evidence bar"). ruamel.yaml's round-trip mode keeps
   comments, key order and quoting.

2. The glossary directory is its own git repo (CLAUDE.md "Data
   durability"), but nothing committed the UI's edits, so that one sat
   uncommitted for six days. commit() records each UI edit as it happens.
   It is best-effort by design: no git binary, not a repo, or a failing
   commit only logs a warning -- a glossary edit must never fail because
   of its version history.
"""

from __future__ import annotations

import fcntl
import io
import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ruamel.yaml import YAML

_logger = logging.getLogger(__name__)

COMMITTER_NAME = "subtitle-ai web UI"
COMMITTER_EMAIL = "subtitle-ai@localhost"


def _yaml(existing_text: str | None = None) -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096  # never re-wrap long alias lists or comments
    mapping, sequence, offset = 2, 2, 0  # love-is-in-the-air.yaml's "- canonical:" style
    if existing_text:
        # Keep whatever list style the file already uses (_turkish.yaml
        # indents its "  - source:" items; the series files don't).
        from ruamel.yaml.util import load_yaml_guess_indent
        _, guessed, guessed_offset = load_yaml_guess_indent(existing_text)
        if guessed is not None and guessed_offset is not None:
            sequence, offset = guessed, guessed_offset
    y.indent(mapping=mapping, sequence=sequence, offset=offset)
    return y


def load(path: Path):
    """Round-trip load: the result behaves like a dict/list but remembers
    its comments, so write() puts them back."""
    return _yaml().load(path.read_text(encoding="utf-8")) or {}


def dumps(data, like: str | None = None) -> str:
    """`like`: the file's current text, to keep its indentation style."""
    buf = io.StringIO()
    _yaml(like).dump(data, buf)
    return buf.getvalue()


@contextmanager
def edit_lock(directory: Path):
    """Serialize glossary read-modify-write operations in this directory.

    flock on the directory descriptor leaves no lock-file artifact in the
    versioned glossary repository; all series share the short critical
    section so files created under different valid names cannot race.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except BaseException:
        os.close(fd)
        raise
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o644)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write(path: Path, data) -> None:
    """Atomic unique-temp write. Call under edit_lock() for read-modify-write."""
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    write_text_atomic(path, dumps(data, like=existing))


def commit(path: Path, message: str) -> bool:
    """Commit just `path` in its directory's git repo. Returns True if a
    commit was made; False (with a log line, never an exception) otherwise.
    Other uncommitted files in the repo are left alone."""
    directory = path.parent
    if shutil.which("git") is None or not (directory / ".git").exists():
        _logger.info("glossary not versioned (no git or not a repo): %s", directory)
        return False
    base = ["git", "-C", str(directory), "-c", f"user.name={COMMITTER_NAME}",
            "-c", f"user.email={COMMITTER_EMAIL}"]
    try:
        subprocess.run(base + ["add", "--", path.name], check=True, capture_output=True, timeout=15)
        staged = subprocess.run(base + ["diff", "--cached", "--quiet", "--", path.name],
                                capture_output=True, timeout=15)
        if staged.returncode == 0:
            return False  # nothing changed
        subprocess.run(base + ["commit", "-q", "-m", message, "--", path.name],
                       check=True, capture_output=True, timeout=15)
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        _logger.warning("glossary commit failed for %s: %s %s", path, exc,
                        detail.decode(errors="replace").strip())
        return False
