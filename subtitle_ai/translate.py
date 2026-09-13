"""NLLB-200 translation: contextual linguistic units in, translated text
out. Does not decide cue timing or segmentation -- that is
segmentation_target.py's job, working on this module's plain text output.
Never reads .en.hi.srt or any external subtitle as a translation source
(audio-first guarantee flows through unbroken: this module only ever sees
text derived from the canonical transcript).
"""

from __future__ import annotations

import gc
import re
from dataclasses import dataclass

from glossary import protect, restore
from transcript import BoundaryReason, MERGEABLE_BOUNDARIES, Segment

NLLB_REPO = "facebook/nllb-200-distilled-1.3B"
NLLB_LANG = {
    "tr": "tur_Latn", "en": "eng_Latn", "es": "spa_Latn", "fr": "fra_Latn",
    "de": "deu_Latn", "ru": "rus_Cyrl", "ar": "arb_Arab", "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    # Added by a real multi-language validation run: this system's stated
    # purpose is to work across languages, not just the ones above, and
    # ASR/language-ID already correctly detects these (e.g. real Hindi
    # audio at 99% confidence) -- only this mapping table was blocking
    # translation. Codes verified against this deployment's own cached
    # NLLB tokenizer vocabulary (tokenizer.json's added_tokens), not typed
    # from memory.
    "hi": "hin_Deva", "ta": "tam_Taml", "te": "tel_Telu", "ml": "mal_Mlym",
    "kn": "kan_Knda", "zh": "zho_Hans", "it": "ita_Latn", "pt": "por_Latn",
    "nl": "nld_Latn", "vi": "vie_Latn", "th": "tha_Thai", "id": "ind_Latn",
    "he": "heb_Hebr", "fa": "pes_Arab", "bn": "ben_Beng", "ur": "urd_Arab",
    "pa": "pan_Guru", "gu": "guj_Gujr", "mr": "mar_Deva", "fi": "fin_Latn",
    "da": "dan_Latn", "no": "nob_Latn", "cs": "ces_Latn", "el": "ell_Grek",
    "ro": "ron_Latn", "ms": "zsm_Latn",
}

MAX_SPAN_CUES = 6
MAX_SPAN_CHARS = 300
MAX_SPAN_GAP = 2.5
_SENTENCE_END = re.compile(r"[.!?…]['\"»)\]]*$")


@dataclass
class TranslationConfig:
    repo: str = NLLB_REPO
    device: str = "cuda"
    max_new_tokens: int = 256
    num_beams: int = 4
    batch_size: int = 12   # confirmed necessary by a real CUDA OOM on a 48-span clip; see translate_batch()
    # Guards against degenerate repetition loops (confirmed real: a Japanese
    # span mentioning "zombie" 3 times produced ~13x "if you're a zombie,
    # you're all zombies" instead of one sentence). 4 was chosen empirically
    # against real NLLB output as the largest (most conservative) size that
    # still fully collapses that loop -- verified to leave legitimate short
    # source repetition (Turkish "Tamam tamam", "Eda! Eda!") byte-identical
    # to the pre-fix output, since those only repeat a 1-2 word span once.
    no_repeat_ngram_size: int = 4


def build_context_spans(cues: list[Segment], real_boundaries: frozenset = frozenset(
        {BoundaryReason.REAL_ACOUSTIC_GAP, BoundaryReason.UTTERANCE_END})) -> list[list[int]]:
    """Group source cue indices into translation-CONTEXT spans -- the unit
    NLLB actually sees per call, so it has whole-sentence context instead
    of translating one cue in isolation ("Eda, sen?" carries no context
    alone). Independent of segmentation_source.py's *display* cue
    boundaries: a real acoustic gap always forces a new span even under
    MAX_SPAN_GAP; a display-only boundary never does."""
    spans, cur, chars = [], [], 0
    for i, cue in enumerate(cues):
        provenance_break = bool(cur) and cue.boundary_before in real_boundaries
        gap_break = bool(cur) and (cue.start - cues[cur[-1]].end) > MAX_SPAN_GAP
        if cur and (provenance_break or gap_break):
            spans.append(cur)
            cur, chars = [], 0
        cur.append(i)
        chars += len(cue.text) + 1
        ends = bool(_SENTENCE_END.search(cue.text.strip()))
        if ends or len(cur) >= MAX_SPAN_CUES or chars >= MAX_SPAN_CHARS:
            spans.append(cur)
            cur, chars = [], 0
    if cur:
        spans.append(cur)
    return spans


def load_model(config: TranslationConfig, src_lang_code: str):
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    # cache_dir + local_files_only=True, explicitly, matching asr.py's
    # explicit download_root="/models" -- without this, huggingface_hub
    # falls back to $HF_HOME/hub, which under the container's non-root
    # `subtitle` user (no home directory) is neither the pre-populated
    # cache at /models/hf nor even a writable path: real-audio validation
    # hit a bare PermissionError on os.makedirs() at the translation
    # stage, on every job regardless of source language, once this was
    # actually exercised end-to-end for the first time in this deployment.
    tok = AutoTokenizer.from_pretrained(config.repo, src_lang=src_lang_code,
                                        cache_dir="/models/hf", local_files_only=True)
    dtype = torch.float16 if config.device == "cuda" else torch.float32
    model = AutoModelForSeq2SeqLM.from_pretrained(config.repo, torch_dtype=dtype,
                                                  cache_dir="/models/hf", local_files_only=True
                                                  ).to(config.device).eval()
    model.generation_config.max_length = None
    bos = tok.convert_tokens_to_ids("eng_Latn")
    return model, tok, bos


def _generate_one_batch(model, tok, bos: int, batch: list[str], device: str,
                        config: TranslationConfig) -> list[str]:
    """Translate one batch, halving it and retrying on CUDA OOM instead of
    failing the whole job. Real bug this fixes, found by a 5-minute
    real-audio test: an earlier version of translate_batch() sent every
    sentence in a job as ONE generate() call -- fine for a handful of
    spans, a confirmed CUDA OutOfMemoryError on a real 48-span clip. A
    batch size that fits comfortably can still blow an 8GB card's budget
    when a few unusually long sentences land in the same call."""
    import torch
    enc = tok(batch, return_tensors="pt", padding=True, truncation=True, max_length=512).to(device)
    try:
        with torch.inference_mode():
            gen = model.generate(**enc, forced_bos_token_id=bos, max_new_tokens=config.max_new_tokens,
                                 num_beams=config.num_beams,
                                 no_repeat_ngram_size=config.no_repeat_ngram_size)
        return tok.batch_decode(gen, skip_special_tokens=True)
    except torch.cuda.OutOfMemoryError:
        del enc
        gc.collect()
        torch.cuda.empty_cache()
        if len(batch) == 1:
            raise
        mid = len(batch) // 2
        return (_generate_one_batch(model, tok, bos, batch[:mid], device, config)
               + _generate_one_batch(model, tok, bos, batch[mid:], device, config))


def translate_batch(model, tok, bos: int, sentences: list[str], device: str,
                    config: TranslationConfig, batch_size: int = 12, on_progress=None) -> list[str]:
    """Chunks `sentences` into batch_size-sized calls (never one call for
    an entire job's worth of spans -- see _generate_one_batch's docstring)
    with OOM-halving retry per chunk. `on_progress(done, total)` fires
    after each chunk -- a long job (hundreds of sentences) previously gave
    no visibility until it finished entirely."""
    if not sentences:
        return []
    out = []
    for i in range(0, len(sentences), batch_size):
        out.extend(_generate_one_batch(model, tok, bos, sentences[i:i + batch_size], device, config))
        if on_progress:
            on_progress(len(out), len(sentences))
    return out


def translate_spans(cues: list[Segment], spans: list[list[int]], src_lang: str,
                    glossary_map: dict[str, tuple[str, str]] | None = None,
                    config: TranslationConfig | None = None, model=None, tok=None, bos: int = None,
                    on_progress=None) -> list[str]:
    """One translated string per span, entity-protected if a glossary is
    supplied. `model`/`tok`/`bos` injectable so pipeline.py controls model
    lifecycle/GPU lock across the whole job, not this function."""
    config = config or TranslationConfig()
    sentences = [" ".join(cues[i].text for i in span) for span in spans]
    payload = [protect(s, glossary_map) for s in sentences] if glossary_map else sentences

    owns_model = model is None
    from contextlib import nullcontext
    from gpu import gpu_lock
    with gpu_lock() if owns_model else nullcontext():
        try:
            if owns_model:
                # Construction inside the try for the same reason as
                # asr.py: a failed load_model() must still reach `finally`.
                model, tok, bos = load_model(config, NLLB_LANG[src_lang])
            translations = translate_batch(model, tok, bos, payload, config.device, config,
                                           batch_size=config.batch_size, on_progress=on_progress)
        finally:
            if owns_model:
                del model
                from gpu import free_gpu
                free_gpu(config.device)
    if glossary_map:
        translations = [restore(t, glossary_map) for t in translations]
    return translations
