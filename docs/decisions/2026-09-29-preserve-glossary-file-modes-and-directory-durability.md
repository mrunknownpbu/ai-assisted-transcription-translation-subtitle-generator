# Preserve glossary file modes and directory durability (2026-09-29)

`glossary_files.write_text_atomic()` now carries forward an existing
target's permission bits instead of replacing them with 0644. After the
atomic replace it also fsyncs the parent directory, making the rename
durable across a crash as well as the already-fsynced file contents.
New files retain the existing 0644 mode. Tests verify a 0640 file stays
0640 and that both the temporary file and containing directory are
fsynced. Production Hammer Session! S01E01 completed with KEEP semantics;
all backed-up SRTs and the glossary suggestion YAML stayed byte-identical,
the YAML remained mode 0644, and no temp files remained. Backup:
`/cache/verification-backups/20260929-atomic-write-durability/`.
