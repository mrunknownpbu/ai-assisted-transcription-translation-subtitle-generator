# Environment variable audit

Method: a script scanned `subtitle_ai/` and `scripts/` for `SUBTITLE_AI_*`, `TVDB_*`, `DATA_PATH`, `CONFIG_PATH`, `TZ`
string literals and compared them with `compose.yml`, `.env.example`, `README.md` and `docs/*.md`.
`TRANSLATE_SERVER_*` variables are read with a different prefix and were checked by hand.

Result: **54 variables read by `subtitle_ai/`**, 51 listed in `.env.example`, 57 mentioned in `compose.yml`.
No variable is set in compose but never read.

## Read by code, not forwarded by `compose.yml`

These all have defaults that already point under the mounted `/cache` or `/models`, so they work without being set.
They are optional overrides, not defects.

| Variable | Default | File |
|---|---|---|
| `SUBTITLE_AI_SRT_UPLOAD_DIR` | `/cache/srt_uploads` | main.py:52 |
| `SUBTITLE_AI_GLOSSARY_SUGGESTIONS_DIR` | `/cache/glossary_suggestions` | main.py:46 |
| `SUBTITLE_AI_CAST_CACHE` | `/cache/cast` | cast_metadata.py:51 |
| `SUBTITLE_AI_ARR_CACHE` | `/cache/arr` | arr_client.py:36 |
| `TVDB_CACHE_DIR` | `/cache/tvdb` | tvdb_client.py:37 |
| `SUBTITLE_AI_MODELS_DIR` | `/models` | turns.py:99 |
| `SUBTITLE_AI_DEDICATED_TRANSLATORS` | built-in table | job_config.py, translate.py |
| `TVDB_API_BASE` | TheTVDB v4 | tvdb_client.py |

## Read by code, absent from `.env.example`

Set by compose directly (no action): `SUBTITLE_AI_DB`, `SUBTITLE_AI_MEDIA_ROOT`, `SUBTITLE_AI_WORK_ROOT`,
`SUBTITLE_AI_GLOSSARY_DIR`, `SUBTITLE_AI_GPU_LOCK`, `SUBTITLE_AI_TRANSCRIPT_CACHE`.
Plus the optional overrides in the table above.

## Read by code, not mentioned in any doc (P3)

Subtitle display limits: `SUBTITLE_AI_MAX_CHARS_PER_LINE`, `SUBTITLE_AI_MAX_LINES`, `SUBTITLE_AI_MAX_CPS`,
`SUBTITLE_AI_TARGET_CPS`, `SUBTITLE_AI_MIN_CUE_DURATION`, `SUBTITLE_AI_MAX_CUE_DURATION`,
`SUBTITLE_AI_TARGET_CUE_DURATION_MIN`, `SUBTITLE_AI_TARGET_CUE_DURATION_MAX`.
Also `SUBTITLE_AI_NLLB_CT2_PATH` and the path overrides above.
Those display limits are not forwarded by compose either, so they can only be changed by editing compose.

## Not a defect, verified

`TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS` and `TRANSLATE_SERVER_DEFAULT_LANG` appear unused to a prefix scan but are read
in `translate_server.py` (blank values are tolerated).

## Not yet checked

Blank-value and invalid-value handling for each variable individually, and whether any default in `.env.example`
differs from the code default. That needs a per-variable pass and is deferred.

No secrets were found in `.env.example`, the working tree, or the full git history (pattern scan for common
token, key and private-key formats).
