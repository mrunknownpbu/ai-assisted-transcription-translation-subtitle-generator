# Remote translation API authentication and limits (2026-09-29)

Before the change, mocked HTTP requests to `POST /translate` succeeded
without authentication for both a 1 MiB+ sentence and a 129-sentence
batch. The endpoint now requires `TRANSLATE_SERVER_API_KEY`, compares
UTF-8 key bytes with `hmac.compare_digest()`, and returns 503 if the key
is not configured. Request bodies are capped at 1 MiB while streaming;
payloads also allow at most 128 nonblank sentences of up to 4096
characters each. The main app's remote client sends the configured key.
Tests cover missing/wrong/correct keys, fixed and streaming body limits,
sentence bounds, and client header propagation. Full suite: 1144 passed,
40 subtests. Both Compose files validate.

No remote translate-server is deployed/configured on this host, so its
HTTP auth path was exercised through the mocked TestClient rather than a
production remote. A production Hammer Session! S01E01 job completed on
the local-translation path (`TRANSLATE_SERVER_URL` unset, `outputs: []`);
both SRTs matched their pre-run backups byte-for-byte. Backup:
`/cache/verification-backups/20260929-translate-server-auth/`.
