#!/usr/bin/env python3
"""One-time: convert the cached NLLB weights to CTranslate2 format for
SUBTITLE_AI_NLLB_BACKEND=ct2 (see translate._generate_one_batch_ct2).

/models is read-only inside the app container, so run this in a
throwaway container with the models directory mounted read-write:

    docker run --rm -e HF_HOME=/tmp/hf -e HF_HUB_OFFLINE=1 \\
        -v /opt/docker/appdata/subtitle-ai/models:/models \\
        -v "$PWD/scripts:/scripts:ro" subtitle-ai:dev python /scripts/convert_nllb_ct2.py

Writes /models/ct2/nllb-200-distilled-1.3B-float16 (~2.6GB, a few seconds).
"""

from __future__ import annotations

import glob
import sys

OUT = "/models/ct2/nllb-200-distilled-1.3B-float16"
SNAPSHOTS = "/models/hf/models--facebook--nllb-200-distilled-1.3B/snapshots/*/config.json"
TOKENIZER_FILES = ["sentencepiece.bpe.model", "tokenizer.json", "tokenizer_config.json",
                   "special_tokens_map.json"]


def main() -> int:
    configs = glob.glob(SNAPSHOTS)
    if not configs:
        print(f"no NLLB snapshot found under {SNAPSHOTS}", file=sys.stderr)
        return 1
    source = configs[0].rsplit("/", 1)[0]

    # ctranslate2 4.4.0's converter reads encoder/decoder.embed_scale, which
    # transformers 4.48 moved onto embed_tokens (M2M100ScaledWordEmbedding).
    # Same value, new home -- point the old attribute at it. Verified by a
    # full-episode output comparison against the HF backend (see
    # benchmark-results/nllb-rtx3070-2026-09-28.json).
    from transformers.models.m2m_100 import modeling_m2m_100 as m2m
    for cls in (m2m.M2M100Encoder, m2m.M2M100Decoder):
        if not hasattr(cls, "embed_scale"):
            cls.embed_scale = property(lambda self: self.embed_tokens.embed_scale)

    import ctranslate2.converters
    ctranslate2.converters.TransformersConverter(source, copy_files=TOKENIZER_FILES).convert(
        OUT, quantization="float16", force=True)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
