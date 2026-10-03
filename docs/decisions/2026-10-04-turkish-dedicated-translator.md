# Decision: Turkish translates with a dedicated model (2026-10-04)

Status: Accepted for Turkish only. Other languages keep NLLB.

## Context

Reading Love Is In The Air S02E01's English showed many mistranslations: names
and pet names (Serkancığım -> "I'm a whore", "Shaggy"), a town (Şile -> Chile),
wordplay (erişte "noodles" -> "Aries", "needles"), tags ("(Eda ses)"). A glossary
fixes single names (done for Şile and Serkancığım) but not this class of error.

## Measurement

`scripts/eval_translation.py` (new `opus` system) translates every source cue
through Workflow B's real code (one cue per span, series glossary, phrase map,
entity recovery) and scores corpus chrF against the human English subtitle
(`.en.hi.srt`, never read by the pipeline) with a paired bootstrap.

First, raw cue text on S02E01 (2,147 pairs, no glossary), four models:

| Model | chrF | vs NLLB 1.3B |
|---|---|---|
| NLLB 1.3B (production) | 54.6 | - |
| NLLB 3.3B int8 | 54.9 | +0.3 [-0.4, +1.0] |
| MADLAD-400 3B int8 | 60.6 | +6.0 [+4.9, +7.1] |
| opus-mt-tc-big-tr-en | 60.8 | +6.2 [+5.3, +7.1] |

MADLAD repeats itself ("my little Serkan" x4, "let him come easy" x3), so it is
unsafe for subtitles. NLLB 3.3B is no better than 1.3B. The 3.3B and MADLAD runs
needed the card free of the app's resident NLLB (8 GB card).

Then through the real pipeline with the glossary:

| Set | NLLB 1.3B | opus-mt, 2 beams | opus-mt, 4 beams |
|---|---|---|---|
| Season 1 E06-10, E20-24, E30-34 (30,274 pairs) | 56.79 | 63.85 (+7.06 [+6.84, +7.30]) | 63.98 (+7.19) |
| S02E01 (2,147 pairs) | 60.86 | 66.89 (+6.03 [+5.16, +6.93]) | 67.19 (+6.33) |

Beams: 4 gains 0.1-0.3 chrF for about 45 % more time; the pipeline's 2 stays.
Time per episode: 27 s (ct2 NLLB) vs 35 s (opus, hf, 2 beams) on S02E01.

## Decision

- `translate.DEDICATED_TRANSLATORS = {"tr": "Helsinki-NLP/opus-mt-tc-big-tr-en"}`.
  `engine_config()` swaps the model per source language inside
  `_translate_sentences`, so a Turkish job uses it and a sentence the code-switch
  detector assigns to another language still goes to NLLB.
- The model runs through transformers (the measured setup, float16, about 0.4 GB
  of weights). A Marian model has no target-language token, so `load_model()`
  returns `NO_FORCED_BOS` and `_generate_one_batch()` passes no forced token.
- Glossary protection, phrase map, entity recovery, run-on retry and orphan
  padding are unchanged: they sit above the model call.
- The remote translate-server only runs NLLB, so a Turkish job ignores
  `TRANSLATE_SERVER_URL` rather than silently using the weaker model.
- `SUBTITLE_AI_DEDICATED_TRANSLATORS=0` reverts to NLLB. The weights live in
  `${CONFIG_PATH}/subtitle-ai/models/hf/models--Helsinki-NLP--opus-mt-tc-big-tr-en`;
  without them a Turkish job fails with the model-load error, as a missing NLLB
  would.

## Limits

- chrF against a human subtitle that paraphrases; not every line was read. The
  blind A/B sheet from the eval is the way to spot-check.
- One series (Love Is In The Air). Widen before adding another language.
- Idioms such as "Kolay gelsin" are still lost by every model measured; they need
  phrase-list entries.
- Existing English subtitles are not regenerated; re-queue a job to get the new
  output.

## Reproduce

```
docker exec -e PYTHONPATH=/app -e HF_HOME=/cache/eval-translation/hf subtitle-ai python /tmp/eval_translation.py \
  --season ".../Season 01" --episodes 6-10,20-24,30-34 --tvdb-id 383383 \
  --system ct2:32x2 --system opus:32x2 --system opus:32x4 --json out.json
```
