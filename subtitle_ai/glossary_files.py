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

import io
import logging
import shutil
import subprocess
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


def write(path: Path, data) -> None:
    """Atomic tmp-then-replace, same as auto_glossary.write_suggestions()."""
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(dumps(data, like=existing), encoding="utf-8")
    tmp.replace(path)


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
