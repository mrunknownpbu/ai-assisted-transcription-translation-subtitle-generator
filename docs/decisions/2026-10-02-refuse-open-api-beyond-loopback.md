# Refuse to start an open API beyond loopback (2026-10-02)

With `SUBTITLE_AI_API_KEY` unset the API is open to anyone who can reach the
port. `subtitle_ai/startup_guard.py` (called first thing in `main.py`) now
refuses to start when the listen address is not loopback, there is no key, and
`SUBTITLE_AI_ALLOW_INSECURE` is not truthy (`1/true/yes/on`).

- The listen address is not known to the app: it is read from `--host` in
  `sys.argv` (the Dockerfile CMD passes `--host 0.0.0.0`), overridden by
  `SUBTITLE_AI_BIND_HOST`, defaulting to uvicorn's `127.0.0.1`. A launcher that
  binds beyond loopback without passing `--host` is not detected.
- The container always binds `0.0.0.0`, so the bundled deployment needs either a
  key or `SUBTITLE_AI_ALLOW_INSECURE=1` in `.env`. compose.yml forwards both
  plus `SUBTITLE_AI_BIND_HOST`.
- This does not change request handling: with a key set, behaviour is as in
  "Reject cross-origin browser mutations when the API key is unset".
- Tests: `tests/test_startup_guard.py`.
