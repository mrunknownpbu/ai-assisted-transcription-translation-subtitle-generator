# ASR quality baselines for Chinese, Japanese, Korean and Thai (2026-10-02)

**Status:** measured; no production setting changed (language pin, VAD, hotwords
and fine-tuned models all tried).

## Question

How accurate is the production transcription (faster-whisper large-v3, default
`AsrConfig`) for the non-Turkish languages, and do the cheap settings help?

## Method

`scripts/eval_transcription.py` scores a fresh transcript (after production
hallucination suppression) against a human subtitle in 60 s windows. New in this
change: `--lang` selects the tokenisation (characters for `ja`/`zh`/`th`, so the
figure is a CER; words otherwise; the Turkish rules apply to `tr` only), katakana is
folded to hiragana, `{...}` tags are stripped, and `--reference-dir` /
`--reference-prefix` read a reference from a file instead of beside the video.

References are the original-language subtitle track embedded in the release
(extracted with ffmpeg; evaluation input only, never given to ASR). Two episodes
per language:

| Language | Title | Track |
|---|---|---|
| Chinese | Exclusive Fairy Tale (2023) S01E01-02 | Chinese (Simplified) |
| Korean | Blood (2015) S01E01-02 | Korean |
| Thai | Thicha (2024) S01E01-02 | Thai |
| Japanese | Human Vapor (2026) S01E01-02 | Japanese (full, not SDH) |

An earlier Japanese check on Happy Kanako's Killer Life S02E01-03 (human `.ja.srt`
uploaded as a translation source) gave 33.4 %, close to Human Vapor.

Not usable as references: most `.ja`/`.ko`/`.ms` files in the library were written
by this app's own video jobs, and the `Subs/` folders of English-language films are
translations. Malay has no baseline yet.

A first scoring pass dropped Thai vowel and tone marks (combining characters), which
a new unit test caught; the figures below are from the corrected scorer, re-run on
the same cached transcripts. Only Thai moved (37.6 -> 37.5 %).

## Baselines (production defaults)

| Language | Error rate | wrong / missed / extra | E01 / E02 |
|---|---|---|---|
| Chinese (CER) | 25.0 % | 8.6 / 12.9 / 3.5 | 27.7 / 22.7 |
| Japanese (CER) | 32.1 % | 8.0 / 13.9 / 10.2 | 29.8 / 34.2 |
| Thai (CER) | 37.5 % | 12.4 / 13.1 / 12.1 | 41.6 / 34.1 |
| Korean (WER) | 52.2 % | 28.8 / 7.1 / 16.3 | 60.4 / 48.0 |

Subtitles condense speech, so absolute numbers are inflated; use them to compare
settings. Korean is per word and is inflated by spacing and spelling variants
(그리구/그리고, 같애/같아); its per-character rate is 38.3 % (E01) and 20.7 % (E02).
Language detection was correct on all eight episodes (confidence 0.56-0.99).

## Variants tried (change in error rate vs baseline, paired bootstrap 95 % interval)

| Language | `language` pinned | VAD 500 ms silence / 300 ms pad |
|---|---|---|
| Thai | -1.3 [-4.5, +1.5] | -1.4 [-4.6, +1.7] |
| Korean | +0.5 [+0.02, +1.1] | **+3.6 [+1.3, +5.9]** |
| Chinese | 0.0 [-0.3, +0.4] | +1.1 [-0.1, +2.4] |
| Japanese | -0.6 [-1.8, +0.5] | +1.3 [-0.8, +3.4] |

## Hotwords (Chinese and Korean)

Does biasing decoding toward names help? `asr:hotwords=...` against the baseline,
same two episodes each, paired bootstrap 95 % interval.

- *Cast names*: what production could supply today. TMDB/TVDB credits for these
  series are romanised ("Ling Chao", "Park Ji-sang"), not in Hanzi or Hangul.
- *Native-script names (oracle)*: Korean 채은아 박지상 유교수 암병원, Chinese 凌 肖,
  taken from the baseline's recurring errors against the human subtitle. This is an
  upper bound, not something production can build; it uses answers a curator would
  have to supply.

| Variant | Korean (WER) | Chinese (CER) |
|---|---|---|
| Baseline | 52.2 % | 25.0 % |
| Cast names, Latin script | 66.3 % (**+14.1** [+9.9, +18.7]) | 26.8 % (**+1.8** [+0.6, +3.5]) |
| Native-script names (oracle) | 52.0 % (-0.2 [-4.1, +3.2]) | 25.4 % (+0.4 [-0.9, +1.8]) |

Latin hotwords clearly hurt both languages (Korean E01 per-character 38.3 -> 53.9 %).
Native-script names cut the targeted errors (凌 -> 林: 21 -> 10) but add new ones that
cancel the gain, and the hotwords leak into the output where nothing was said
("the" -> 유교수, "자막제공" -> 박지상, "bloody" -> 암병원). This agrees with the Turkish
measurement recorded in the `asr.py` docstring (Title Case output, dropped windows):
`SUBTITLE_AI_ASR_HOTWORDS` stays off. Name errors are better handled by the
translation-time glossary, which does not touch how the audio is read.

## Fine-tuned models (Turkish and Japanese)

Community CTranslate2 fine-tunes, downloaded to a scratch directory in the container
and loaded by path through `AsrConfig(model_name=...)`; production is not wired to
them. Alternatives were pinned to the language; the baseline auto-detects (pinning
made no reliable difference earlier). Paired bootstrap 95 % interval against
production large-v3.

Turkish, Love Is In The Air S01E01-03 (human subtitle):

| Model | WER | vs production | Name recall |
|---|---|---|---|
| large-v3 (production) | 17.3 % | | 87.0 % |
| large-v3-turbo (control, not fine-tuned) | 17.1 % | -0.2 [-0.8, +0.4] | 87.2 % |
| drascom/whisper-large-v3-turbo-turkish-ct2 | 30.7 % | **+13.4** [+12.2, +14.1] | 66.7 % |
| vincespeed/faster-whisper-large-v3-turbo-turkish | 30.7 % | **+13.4** [+12.2, +14.0] | 66.6 % |

Both Turkish fine-tunes are based on turbo, so the control separates "turbo" from
"fine-tuned": turbo alone matches large-v3, the fine-tuning is what hurts (more
wrong words, 15.0 % against 5.6 %, and worse name recall).

Japanese, Human Vapor S01E01-02 (embedded Japanese track), CER:

| Model | CER | Result |
|---|---|---|
| large-v3 (production) | 32.1 % | |
| kotoba-tech/kotoba-whisper-v2.0-faster | n/a | crashed (`MemoryError: std::bad_alloc`) in faster-whisper's word-timestamp alignment; the pipeline requires word timestamps, so it cannot run here as is (cause not investigated) |
| JhonVanced/whisper-large-v3-japanese-4k-steps-ct2 | 95.2 % (+63.1 [+57.9, +68.9]) | collapsed into repetition loops ("どうどうどう...": 117-134 segments carrying 11-12k words against about 550 segments and 4.7k words); 77.5 % of the reference missed |

Conclusion: none of the four fine-tunes is usable; production large-v3 stays for
Turkish and Japanese. A fine-tune is not an upgrade by default: each must be scored
against the production model on the same episodes first. Untried: other Japanese
models (e.g. kotoba v2.1+ with alignment heads), a Thai fine-tune, and
large-v3-turbo as a speed option (accuracy matched here; speed not measured).

## Decision

- Nothing changes. Pinning the language is neutral (detection is already right) and
  shorter VAD silence is worse for Korean and not better elsewhere; the defaults
  (`vad_min_silence_ms=1000`, `vad_speech_pad_ms=500`) stay. Do not tune VAD per
  language on this evidence.
- The dominant errors are not setting-related: Chinese and Korean lose proper names
  (凌 -> 林 x21, 채은아 -> 채연아 x7), Thai confuses similar consonants (ร/ล,
  ม/อ), Chinese mixes Simplified/Traditional forms (a scoring effect more than a
  translation one).
- Hotwords stay off (see above); name errors are left to the translation-time
  glossary.
- Fine-tuned Turkish and Japanese models are worse (see above); a Thai fine-tune is
  still untested. Two episodes per language is a small sample; widen it
  before adopting anything.

## Reproduce

Extract the track (`ffmpeg -i <video> -map 0:<n> -c:s srt ja-E01.srt`), then inside
the container:

    python scripts/eval_transcription.py --season <Season dir> --episodes 1-2 --lang ja \
      --reference-dir <dir> --reference-prefix ja --system asr: --system asr:language=ja \
      --work /cache/eval-asr/base-ja

Each `asr:` system is ~6 minutes of GPU per episode.
