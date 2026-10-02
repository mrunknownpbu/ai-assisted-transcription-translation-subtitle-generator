# Document and test Origin checks behind TLS proxies (2026-09-29)

The Origin guard compares against Starlette's ASGI `request.base_url`;
the app does not directly interpret `X-Forwarded-Proto` or
`X-Forwarded-Host`. Tests now exercise a matching HTTPS ASGI scope and
confirm attacker-supplied forwarded headers alone cannot make an HTTPS
Origin pass against an HTTP scope. README deployment guidance says to
trust forwarded scheme headers only from actual proxy IPs, overwrite
forwarded headers at the proxy, and preserve the external `Host`. The
bundled Compose deployment has no TLS proxy. Full suite: 1166 passed,
40 subtests.

After deployment, a production Hammer Session! S01E01 POST completed
with KEEP semantics (`outputs: []`); `.ja.srt`, `.en.srt`, and
`.en.hi.srt` matched the pre-run backups byte-for-byte. The service
remained healthy. Backup:
`/cache/verification-backups/20260929-proxy-origin/`.
