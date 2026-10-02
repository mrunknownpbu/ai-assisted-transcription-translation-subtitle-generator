# Residual gaps and long repetition loops (2026-09-29)

Corrected the earlier short-sparse scan to read cached word `text` (not
the faster-whisper-only raw `word` key). Across the latest 23 transcripts,
78 segments were 10-15s long with at most 1 character/sec. Five occur in
Hammer Session! S01E01 and one in Love Is In The Air S01E01. In Hammer,
the 882.9-895.0s and 3192.2-3203.6s spans have internal word-timing holes
and overlap human-reference cues that had no source coverage. Added a
stricter 10-15s recovery threshold (1 character/sec); these spans are
re-decoded with VAD off and replace the original only when they yield
more text.

The same corrected scan found a separate, more severe residual case:
the latest-cache distribution had one segment at compression_ratio >=
7.4, Hammer S01E01 3669.1-3707.0s (14.87), repeating sushi-menu text over
human dialogue. A direct isolated VAD-off decode recovered 10 normal
segments (compression ratios 1.03-1.23), including dialogue throughout
the human-reference gap. Long segments at this ratio are now retried;
their original is replaced only if the retry is nonempty and every
recovered segment is below the configured compression-ratio threshold.
This is independent of the graduated hallucination score: it recovers
the underlying speech before post-ASR hallucination filtering.

Verified with a fresh production POST `/api/jobs` on S01E01 (manual
Japanese, audio stream 1), pipeline 2.0.7: the ASR cache missed, the
full run took 218.5s, recovered 863 segments, suppressed zero, passed
validation, and wrote both `.ja.srt` and `.en.srt` on a follow-up cache-hit
run (7.6s). Before/after on the backed-up library pair: source cues
628 -> 631; English cues 614 -> 614; uncovered human cues 104/756
(13.8%) -> 102/756 (13.5%); chrF 24.49 -> 24.49 over 366 pairs. Of the
104 formerly uncovered cues, 17 gained source coverage and 15 previously
covered cues lost it. Because the pipeline-version bump forced a fresh
full-episode ASR pass, this net change is confounded by ASR variation and
does not establish that the recovery alone improved total coverage.
The 882.9-895.0s and 3192.2-3203.6s cases now have source and English
cues; the repeated-text span now has dialogue rather than the suppressed
loop. Current pipeline-2.0.7 hallucination scan: p99.9 compression ratio
2.12, zero segments >=2.4, one loop-shaped segment at 1.84, and no new
suppression under `--what-if-span 10`.

The exact "Oh, oh, my God..." sequence was not present in the current or
backed-up S01E01 outputs; no generic repetition suppressor was added.
Remaining gap coverage is 13.5%. Backups taken before production jobs are
in `/cache/verification-backups/20260929-short-sparse-vad-recovery/`,
`20260929-short-sparse-and-loop-recovery/`, and
`20260929-item3-cache-publish/`.
