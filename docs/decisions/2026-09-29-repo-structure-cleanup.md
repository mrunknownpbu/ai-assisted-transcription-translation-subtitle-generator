# Repo structure cleanup (2026-09-29)

Removed `archive/` (3 unreferenced prototype zips: an old GUI mockup, an
old platform build, and a remote-asr-server prototype superseded by
`translate_server.py` -- none mentioned anywhere in code or docs). Moved
`IMPROVEMENT_PLAN.md` and `ENHANCEMENT_DRAFT.md` (both fully completed,
frozen planning rounds) from the repo root into `docs/`, since this file
is now the live changelog; updated the handful of path references in
`README.md`/this file (left the many inline code-comment citations, e.g.
`# IMPROVEMENT_PLAN.md 4.2`, as historical labels rather than rewriting
dozens of files for a path move). Added `.pytest_cache/` to `.gitignore`
(untracked but unignored before -- a stray `git add -A` could have picked
it up). Full suite: 1166 passed, 40 subtests.
