# Worker loop exception containment (2026-09-29)

`Worker.run()` now logs and isolates failures from work-root sweeping,
job claiming, idle cast refresh, and job processing. If an exception
escapes `_process()` (including a database error while its own failure
handler tries `store.finish()`), the worker makes one logged attempt to
mark the job failed, logs any failure of that write separately, then
continues polling instead of terminating its daemon thread. Regression
tests reproduce a locked-database claim and a locked `finish()` during
error handling; in both cases the loop reaches a second claim. Targeted
worker tests: 73 passed. Full suite: 1129 passed, 38 subtests.
