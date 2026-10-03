# Decision log

Dated, evidence-based records of what was built, measured, shipped, shipped-but-off, or deliberately deferred. Moved verbatim out of `CLAUDE.md` so that file stays short; code comments and docs that cite `CLAUDE.md ("<title>")` mean the entry below with that title. Newest last.

| Date | Entry |
|---|---|
| 2026-09-28 | [Cast metadata: TVDB + TMDB + IMDb feed name protection (since 2026-09-28)](2026-09-28-cast-metadata-tvdb-tmdb-imdb-feed-name-protection.md) |
| 2026-09-28 | [ASR initial_prompt style experiment: measured, NOT adopted (2026-09-28)](2026-09-28-asr-initial-prompt-style-experiment-measured-not-adopted.md) |
| 2026-09-28 | [Speaker-turn detection: implemented, measured, shipped OFF (2026-09-28)](2026-09-28-speaker-turn-detection-implemented-measured-shipped-off.md) |
| 2026-09-28 | [Sonarr / Radarr / Plex / Jellyfin (since 2026-09-28)](2026-09-28-sonarr-radarr-plex-jellyfin.md) |
| 2026-09-28 | [English span distribution: sentence-aware, not word-count-fraction (2026-09-28)](2026-09-28-english-span-distribution-sentence-aware-not-word-count-frac.md) |
| — | [Readability vs. content-completeness: an accepted, disclosed tradeoff](readability-vs-content-completeness-an-accepted-disclosed-tr.md) |
| — | [Unpunctuated run-on source cues: shipped](unpunctuated-run-on-source-cues-shipped.md) |
| 2026-09-28 | [VAD dropping whole gaps in full-episode decoding: recovered (2026-09-28)](2026-09-28-vad-dropping-whole-gaps-in-full-episode-decoding-recovered.md) |
| 2026-09-29 | [Residual gaps and long repetition loops (2026-09-29)](2026-09-29-residual-gaps-and-long-repetition-loops.md) |
| 2026-09-29 | [Remote translation retry fallback (2026-09-29)](2026-09-29-remote-translation-retry-fallback.md) |
| 2026-09-29 | [Worker loop exception containment (2026-09-29)](2026-09-29-worker-loop-exception-containment.md) |
| 2026-09-29 | [Atomic SRT temp-file uniqueness (2026-09-29)](2026-09-29-atomic-srt-temp-file-uniqueness.md) |
| 2026-09-29 | [SRT editor input and active-job guard (2026-09-29)](2026-09-29-srt-editor-input-and-active-job-guard.md) |
| 2026-09-29 | [Job path normalization and media validation (2026-09-29)](2026-09-29-job-path-normalization-and-media-validation.md) |
| 2026-09-29 | [API-key coverage for job creation and audio analysis (2026-09-29)](2026-09-29-api-key-coverage-for-job-creation-and-audio-analysis.md) |
| 2026-09-29 | [Remote translation API authentication and limits (2026-09-29)](2026-09-29-remote-translation-api-authentication-and-limits.md) |
| 2026-09-29 | [SRT upload retention (2026-09-29)](2026-09-29-srt-upload-retention.md) |
| 2026-09-29 | [Bound audio-analysis waits on the GPU lock (2026-09-29)](2026-09-29-bound-audio-analysis-waits-on-the-gpu-lock.md) |
| 2026-09-29 | [Match bracketed sibling subtitle names in `/api/media` (2026-09-29)](2026-09-29-match-bracketed-sibling-subtitle-names-in-api-media.md) |
| 2026-09-29 | [Serialize glossary edits and cast-enrichment writes (2026-09-29)](2026-09-29-serialize-glossary-edits-and-cast-enrichment-writes.md) |
| 2026-09-29 | [Cache cast-enrichment bookkeeping (2026-09-29)](2026-09-29-cache-cast-enrichment-bookkeeping.md) |
| 2026-09-29 | [Avoid duplicate sibling-subtitle sorting (2026-09-29)](2026-09-29-avoid-duplicate-sibling-subtitle-sorting.md) |
| 2026-09-29 | [Preserve glossary file modes and directory durability (2026-09-29)](2026-09-29-preserve-glossary-file-modes-and-directory-durability.md) |
| 2026-09-29 | [Make job cancellation and deletion status checks atomic (2026-09-29)](2026-09-29-make-job-cancellation-and-deletion-status-checks-atomic.md) |
| 2026-09-29 | [Fire failure webhooks only on transition into failed (2026-09-29)](2026-09-29-fire-failure-webhooks-only-on-transition-into-failed.md) |
| 2026-09-29 | [Reject cross-origin browser mutations when the API key is unset (2026-09-29)](2026-09-29-reject-cross-origin-browser-mutations-when-the-api-key-is-un.md) |
| 2026-09-29 | [Document and test Origin checks behind TLS proxies (2026-09-29)](2026-09-29-document-and-test-origin-checks-behind-tls-proxies.md) |
| 2026-09-29 | [Master push (2026-09-29)](2026-09-29-master-push.md) |
| 2026-09-29 | [Repo structure cleanup (2026-09-29)](2026-09-29-repo-structure-cleanup.md) |
| 2026-09-29 | [Code-switched dialogue mistranslated: per-sentence language override (2026-09-29)](2026-09-29-code-switched-dialogue-mistranslated-per-sentence-language-o.md) |
| 2026-09-29 | [auto_glossary.py: per-language mining, not Turkish-only (2026-09-29)](2026-09-29-auto-glossary-py-per-language-mining-not-turkish-only.md) |
| 2026-09-29 | [auto_glossary.py: Korean and Chinese added, Thai investigated and deferred (2026-09-29)](2026-09-29-auto-glossary-py-korean-and-chinese-added-thai-investigated.md) |
| 2026-09-30 | ["If You Love" (2023): lead character's name translated as "Fire" -- stale cast-enrichment evidence, not a code bug (2026-09-30)](2026-09-30-if-you-love-lead-character-s-name-translated-as-fire-stale-c.md) |
| 2026-09-30 | [Turkish glued case-suffix protection: explicit, measured opt-in (2026-09-30)](2026-09-30-turkish-glued-case-suffix-protection-explicit-measured-opt-i.md) |
| 2026-09-30 | [Cast enrichment retries when subtitle evidence grows (2026-09-30)](2026-09-30-cast-enrichment-retries-when-subtitle-evidence-grows.md) |
| 2026-09-30 | [Cast metadata refresh default: 12 hours (2026-09-30)](2026-09-30-cast-metadata-refresh-default-12-hours.md) |
| 2026-10-02 | [Refuse to start an open API beyond loopback (2026-10-02)](2026-10-02-refuse-open-api-beyond-loopback.md) |
| 2026-10-02 | [Workflow A uses no subtitle text as input](2026-10-02-audio-only-asr-inputs.md) |
| 2026-10-02 | [POST /api/srt-translations requires the API key](2026-10-02-srt-translations-endpoint-was-unauthenticated.md) |
| 2026-10-02 | [Subtitle timing, the reported ~5.1 s offset, and how it is tested](subtitle-timing.md) |
| — | [Architecture audit and rebuild strategy](2026-10-02-architecture-audit.md) |
| — | [QC is advisory, not a gate -- on purpose](qc-is-advisory-not-a-gate.md) |
| — | [Entity-protection precedent: the evidence bar](entity-protection-evidence-bar.md) |
| 2026-10-02 | [Every job transcribes the audio afresh](2026-10-02-every-job-transcribes-afresh.md) |
| 2026-10-02 | [ASR quality baselines for Chinese, Japanese, Korean and Thai](2026-10-02-asr-quality-baselines-zh-ja-ko-th.md) |
| 2026-10-03 | [Re-time an existing subtitle against the audio](2026-10-03-subtitle-retiming.md) |
| 2026-10-04 | [Turkish translates with a dedicated model](2026-10-04-turkish-dedicated-translator.md) |
