"""Refuse to start an unauthenticated API on a non-loopback address.

With `SUBTITLE_AI_API_KEY` unset the API is open: anyone who can reach the
port can queue GPU jobs, read job data and edit glossaries. That is fine on
loopback, and was a deliberate choice for a trusted LAN, but it should be an
explicit one -- so a non-loopback bind with no key now fails at startup
unless `SUBTITLE_AI_ALLOW_INSECURE` acknowledges it.

The bind address belongs to whatever launches uvicorn, not to this app, so
it is recovered from `--host` on the command line (the container's CMD uses
it) or from `SUBTITLE_AI_BIND_HOST` for launchers that don't pass it that
way. With neither, uvicorn's own default (127.0.0.1) is assumed.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence

DEFAULT_HOST = "127.0.0.1"
_TRUE = {"1", "true", "yes", "on"}


class InsecureBindError(RuntimeError):
    pass


def bind_host(argv: Sequence[str], env: Mapping[str, str]) -> str:
    """The address the server is told to listen on (`SUBTITLE_AI_BIND_HOST`
    wins over `--host`)."""
    override = env.get("SUBTITLE_AI_BIND_HOST", "").strip()
    if override:
        return override
    for i, arg in enumerate(argv):
        if arg == "--host" and i + 1 < len(argv):
            return argv[i + 1].strip()
        if arg.startswith("--host="):
            return arg.split("=", 1)[1].strip()
    return DEFAULT_HOST


def is_loopback(host: str) -> bool:
    host = host.strip().strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # a hostname, or empty/wildcard: reachable beyond this machine


def check_bind_safety(argv: Sequence[str], env: Mapping[str, str]) -> None:
    """Raise InsecureBindError if the API would be open beyond loopback."""
    host = bind_host(argv, env)
    if is_loopback(host) or env.get("SUBTITLE_AI_API_KEY", ""):
        return
    if env.get("SUBTITLE_AI_ALLOW_INSECURE", "").strip().lower() in _TRUE:
        return
    raise InsecureBindError(
        f"refusing to start: listening on {host!r} with no SUBTITLE_AI_API_KEY would leave "
        "the API open to anyone who can reach it. Set SUBTITLE_AI_API_KEY, bind to "
        "127.0.0.1 behind an authenticating reverse proxy, or set "
        "SUBTITLE_AI_ALLOW_INSECURE=1 to accept an open API on a trusted network.")
