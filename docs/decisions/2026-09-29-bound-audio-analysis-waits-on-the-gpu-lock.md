# Bound audio-analysis waits on the GPU lock (2026-09-29)

Before the change, a reproduced `/api/audio-streams` request stayed
blocked for the full time another thread held `gpu_lock()` and returned
200 only after release. `gpu_lock(timeout=...)` now supports bounded
acquisition while its no-argument, blocking behavior remains unchanged
for pipeline workers. Audio analysis requests use a nonblocking
acquisition and return 503 with `Retry-After: 1` when a job owns the GPU.
Tests cover immediate failure under real cross-thread lock contention
and the endpoint's retryable response without entering the sampler path.
Full suite: 1151 passed, 40 subtests.

After deployment, a production Hammer Session! S01E01 job completed with
existing-output KEEP semantics (`outputs: []`). Three live
`GET /api/audio-streams` probes during its GPU-locked translation stage
returned 503 in 0.003s, 0.014s, and 0.119s. The `.ja.srt`, `.en.srt`,
and `.en.hi.srt` files matched their pre-run backups byte-for-byte.
Backups: `/cache/verification-backups/20260929-audio-stream-lock/`.
