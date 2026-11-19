# Findings and remediation

Source of truth for each finding is `INITIAL_AUDIT.md` section 13. This file tracks what was changed.

| ID | Sev | Change | Files | Tests |
|---|---|---|---|---|
| F-01 | P2 | Audio extraction writes to a `.part` temp file with `-f wav`, renames on success, deletes it on failure; `-nostdin` added; `OSError` during the rename now surfaces as `MediaError` | `subtitle_ai/media.py` | `tests/test_media_extract.py` (5 tests), real-ffmpeg check |
| F-10 | P2 | SSE hook retries non-auth failures with 2 s to 30 s backoff and stops cleanly on 401/403 | `frontend/src/api/useEventStream.ts` | `useEventStream.test.tsx` (+2 tests) |

Open, not changed: F-02 (cancel during ffmpeg), F-03 (Python dependency advisories), F-04 (react-router advisories),
F-05 (undocumented variables), F-06 (no LICENSE), F-07 to F-09, F-11 to F-13.

Environment variable semantics, API contracts, database schema and output conventions were not changed.
Nothing here has been committed, pushed, built into the CUDA image or deployed.
