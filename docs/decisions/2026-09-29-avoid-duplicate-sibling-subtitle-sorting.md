# Avoid duplicate sibling-subtitle sorting (2026-09-29)

`/api/media` previously sorted candidate names in the shared sibling scan,
then sorted the resolved output paths again. The scan now supports a
resolved-path ordering mode: `/api/media` skips the name sort and sorts
the final paths once, preserving exact response ordering even when
in-root symlinks resolve elsewhere. `/api/browse` retains its prior
filename order. Regression tests cover bracketed names and reversed
symlink-target ordering. Media/browse tests: 27 passed. Production
`/api/media` returned the `.ja.srt`, `.en.srt`, and `.en.hi.srt` paths
in sorted order; a Hammer Session! S01E01 POST completed with KEEP
semantics (`outputs: []`) and all three subtitles matched backups:
`/cache/verification-backups/20260929-sibling-sort/`.
