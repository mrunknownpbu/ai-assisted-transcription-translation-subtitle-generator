# Remote translation retry fallback (2026-09-29)

`remote_translate_batch()` now rejects malformed replies unless each
remote chunk returns exactly one string per submitted sentence. The
input/output mappings use strict `zip()` so any internal count mismatch
fails visibly instead of silently truncating. In `translate_spans()`,
`remote_succeeded` is set only after the optional run-on chunk retry has
also succeeded; if that retry fails, the whole payload goes through the
existing local fallback instead of producing `None` translations. Added
regressions for short remote replies and for a successful primary remote
call followed by a failed retry; the latter confirms local translation
is called and its result is returned. Full suite: 1127 passed, 38
subtests. The production container has no `TRANSLATE_SERVER_URL`, so a
production job would exercise only local translation and cannot verify
this remote-only failure path; the isolated remote integration tests are
the available verification for this deployment.
