# Decision: Workflow A uses no subtitle text as input

Date: 2026-10-02

Status: Accepted

## Context

Workflow A (video to English) treats the audio stream as the only source of
truth for the transcript. Existing subtitles, filenames, embedded subtitle
tracks and other text may supply metadata or evidence, but must not become
transcription input, influence ASR, or repair ASR output. Workflow B is the
opposite by design: a subtitle is its source document and no ASR runs.

## Problem

An audit of the code (2026-10-02) found two paths where subtitle text from
files on disk reached Workflow A's ASR or its output:

1. `name_correction.series_vocabulary()` read every `*.{lang}.srt` under the
   series folder to decide which words are ordinary vocabulary, and the
   result gated an edit of the ASR transcript. Name correction is on by
   default.
2. `Worker._load_auto_hotwords()` passed names mined from the same subtitle
   files to `pipeline.run(extra_hotwords=...)`, where they were unioned into
   ASR `hotwords`. Hotwords are off by default (`SUBTITLE_AI_ASR_HOTWORDS`),
   so this only acted when an operator opted in.

Sibling subtitles may be this pipeline's earlier output, a human subtitle, or
a downloaded fansub; the code could not tell which.

## Evidence

Measured with `scripts/eval_transcription.py` on Love Is In The Air S01E01-E04
against the human Turkish subtitles (`benchmark-results/name-vocabulary-source-ab-2026-10-02.json`).
Only these four episodes have a production-settings cached transcript.

| Variant | Name corrections confirmed / unconfirmed | Name recall | WER |
|---|---|---|---|
| No correction | n/a | 86.2% | 17.9% |
| Vocabulary from subtitle files (old) | 41 / 4 | 88.3% | 17.8% |
| Vocabulary from cached ASR transcripts (new) | 48 / 4 | 88.6% | 17.8% |

The same four corrections are unconfirmed in both variants. The new source
confirmed seven more `Aydın -> Aydan` fixes: the old vocabulary, built from the
human subtitles, vetoed them because "aydın" occurs lowercase there.

Not measured: a series with few cached episodes. The vocabulary here draws on
the four S01 transcripts plus five S02 transcripts; the old source drew on 39
subtitle episodes. With little cache the lowercase-word veto weakens, so more
ordinary capitalised words become candidates for correction (the original
`Senin -> Selin` failure that the veto was added to stop). The episode's own
transcript still contributes to the veto.

## Options considered

1. Build the series vocabulary from cached ASR transcripts (chosen).
2. Disable name correction and subtitle-mined hotwords by default.
3. Keep the behaviour and document it as a bounded exception.

## Decision

- Option 1 for name correction: `series_vocabulary(series_root, language,
  cache_dir, exclude_media)` reads the transcript cache (audio-derived), one
  entry per episode (the newest), skipping suppressed segments. It peeks each
  cache file's header and parses only the series' own files.
- Subtitle-mined names no longer feed ASR at all. `extra_hotwords` is removed
  from `pipeline.run()` and the worker. Mining still runs and still writes the
  Series page suggestions file for human review, which never reaches ASR or
  translation protection. Curated glossary entries (human reviewed or backed
  by cast metadata) may still bias ASR when hotwords are enabled.

## Consequences

- A series with few cached transcripts gets a weaker ordinary-word veto until
  more episodes are processed. This is a known limitation, not yet measured on
  a cold series.
- Cached transcripts from earlier pipeline versions are used as is.
- The Series suggestions UI still reads subtitle files; that is metadata for
  humans, not Workflow A input.
- `eval_transcription.py` takes `--cache-dir` and uses the same vocabulary
  source as production.

## Rejected alternatives

- Disable by default: loses a measured gain (about 15% of character-name
  mentions were being lost before correction).
- Documented exception: contradicts the invariant as written.

## Validation

- `tests/test_name_correction.py`: the vocabulary comes from the cache; subtitle
  files are never read; other series, other languages and suppressed segments
  are ignored; only the newest entry per episode counts; the memo refreshes when
  cache files change. A pipeline test makes `srt.parse` raise and shows a
  subtitle full of lowercase "aydın" neither vetoes nor is parsed.
- `tests/test_pipeline_language.py`, `tests/test_worker.py`: `extra_hotwords` is
  gone; mined names never reach the pipeline call.
- Real data: the vocabulary for a Love Is In The Air S02 episode loads in 0.37 s
  (9,633 distinct words) and is memoised thereafter.
