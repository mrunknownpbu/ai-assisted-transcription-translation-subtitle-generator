# Final production report (2026-11-18)

## Summary

The application is built and running on this host. Its tests pass, and the deployed container passed every
check listed in `TEST_REPORT.md`. The audit is **not complete**: about half of the backend modules were not read, no
benchmark or GPU optimisation was done, and several reports in the original brief were not written.

## 1. Original assessment

A mature, well-tested, well-documented codebase: 1,436 backend and 108 frontend tests, lint and mypy with no
exclusions, green CI, no secrets in history, a coherent security model, atomic output, finite crash recovery.

## 2-3. Defects found and fixed

| ID | Sev | Defect | Fix |
|---|---|---|---|
| F-01 | P2 | A killed or timed-out ffmpeg could leave a truncated WAV at its final path | temp file plus atomic rename, `-nostdin`, 5 tests, real-ffmpeg check |
| F-10 | P2 | Live updates stopped for good after any HTTP error during a server restart | reconnect with backoff, no retry on 401/403, 2 tests (failed before the fix) |

No P0 or P1 defect was confirmed in the code read. Open items (F-02 cancel during ffmpeg, F-03 Python dependency
advisories, F-04 react-router advisories, F-05 to F-09, F-11 to F-13) are in `INITIAL_AUDIT.md` and
`FINDINGS_AND_REMEDIATION.md`.

## 4. Files modified

`subtitle_ai/media.py`, `frontend/src/api/useEventStream.ts`, `frontend/src/api/useEventStream.test.tsx`,
`tests/test_media_extract.py` (new), `docs/audit/*` (new). Nothing else in the repository changed.

## 5. Dependencies changed

None. The advisories listed in F-03/F-04 were deliberately not touched (torch, transformers and CTranslate2 are
pinned together, and a bump needs a GPU regression run).

## 6. Security

No security change was needed in the code. Verified on the running container: key required on every route except
health, constant-time compare, traversal attempts refused on assets and browse, uploads limited, non-root user,
read-only model mount. Hardening applied in deployment: the media mount is `/data/media`, not the whole NAS share; `.env` is mode 600 and git-ignored.
Remaining exposure: plain HTTP on all interfaces, one shared key, no TLS.

## 7. GPU

Verified: the GPU is visible in the container and used by both Whisper and CTranslate2. One 3-minute clip showed a peak of
4255 MiB VRAM, up to 98 % utilisation and 58 C. **No optimisation or tuning was performed**; the repository's
dedicated-GPU defaults are in use. The documented comparison table (batch size, beams, residency) was not run.

## 8. Test results

See `TEST_REPORT.md`: lint, mypy, 1,441 backend tests, 110 frontend tests, frontend build, advisory scans, and the deployment checks above.

## 9. Benchmarks

None run in this audit. Earlier numbers in the repository's `benchmark-results/` are the project's own, not re-verified here.

## 10. Docker and deployment status

Image built (10.4 GB). Container healthy, restarts surviving, restart policy `unless-stopped`. Not done: daily backup cron
(command given in `BACKUP_AND_RECOVERY.md`), TLS, any integration, a full-length episode run.

## 11. Known limitations and remaining risks

- The image comes from uncommitted changes; commit them to make the deployment reproducible.
- Ten test jobs from the checks remain in the job history; they point at the scratch library, not the real one.
- Unread modules: `qc/`, `projection.py`, `segmentation_source.py`, `glossary*`, `cast_*`, `name_correction.py`, `retime*.py`,
  `turns.py`, `arr_client.py`, `tvdb_client.py`, most frontend pages.
- Whisper and NLLB quality on this host has not been measured against human subtitles by the repository's evaluation scripts.
- The browser UI was not opened or visually checked.
- Malay and other non-Turkish hallucinations are not suppressed by the signature registry (documented in the repo).

## 12. Rollback

`docker tag subtitle-ai:2026-11-18-audit1 subtitle-ai:dev && docker compose up -d subtitle-ai`, or restore the database
and `.env` as described in `BACKUP_AND_RECOVERY.md`. There was no earlier deployment on this host to roll back to.

## 13. Recommended next actions

1. Commit the audit branch (and decide whether to push it).
2. Install the daily backup cron line.
3. Run one full episode with the repository's evaluation scripts to get real timing, VRAM and quality numbers.
4. Triage F-03 (starlette, python-multipart) for reachability.
5. Finish the unread modules.
