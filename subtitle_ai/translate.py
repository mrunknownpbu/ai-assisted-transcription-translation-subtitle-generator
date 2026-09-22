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

from glossary import (_phrase_key, bare_entity_translation,
                      entity_occurrence_report, is_unpunctuated_run_on,
                      join_multi_speaker_dash_lines, protect,
                      repair_corrupted_placeholders, restore,
                      split_into_sentences, split_multi_speaker_dash_lines)
from transcript import BoundaryReason, MERGEABLE_BOUNDARIES, Segment

REMOTE_TIMEOUT_SECONDS = 60.0


class RemoteTranslationError(Exception):
    """The remote translate-server (see translate_server.py) was
    unreachable, timed out, or returned an error. Callers catch this and
    fall back to local load_model()/translate_batch() -- never let a
    media-server hiccup break a translation job outright, just make it
    slower (see translate_spans()'s remote_url handling)."""


def remote_translate_batch(url: str, sentences: list[str], src_lang: str,
                           batch_size: int, on_progress=None) -> list[str]:
    """Same chunking/progress-callback shape as translate_batch(), but
    dispatches each chunk to a remote translate-server over HTTP instead
    of a local model call. Real motivation (2026-09-20 benchmark, same
    model/config/sentences): a media server's RTX 3070 measured ~8x the
    throughput of this host's Tesla P4 (16.53 vs 2.05 sentences/sec) --
    NLLB-200-distilled-1.3B has real tensor-core acceleration there this
    card doesn't have."""
    import httpx
    if not sentences:
        return []
    out: list[str] = []
    try:
        with httpx.Client(timeout=REMOTE_TIMEOUT_SECONDS) as client:
            for i in range(0, len(sentences), batch_size):
                chunk = sentences[i:i + batch_size]
                resp = client.post(f"{url}/translate", json={"sentences": chunk, "src_lang": src_lang})
                resp.raise_for_status()
                out.extend(resp.json()["translations"])
                if on_progress:
                    on_progress(len(out), len(sentences))
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise RemoteTranslationError(f"remote translate-server at {url!r} failed: {exc}") from exc
    return out

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

# Phase 2 scoping (see the Alptekin "Moon Flood" investigation, 2026-09-21):
# a fixed word-count chunk size used ONLY as a bounded, content-preserving
# retry input for spans glossary.is_unpunctuated_run_on() flags (no real
# sentence boundary to split on). 6 guarantees 2+ chunks for anything that
# cleared the 8-word flagging threshold. NOT YET wired into
# _translate_sentences()'s main flow -- these two helpers exist standalone
# for the validation pass (compare real chunked vs. real one-shot NLLB
# output over the live corpus) before deciding whether to enable them.
CHUNK_WORDS = 6


def _chunk(text: str) -> list[str]:
    """Splits already-protect()-ed text into fixed-size word groups, each
    given an artificial trailing "." so NLLB gets a sentence-shaped input
    instead of one long run-on. Chunk boundaries are arbitrary word
    cuts, not real clause boundaries -- this trades fluency for a bound
    on how much unrelated content can land in one generate() call, it
    does not guarantee grammatically correct chunking. Splitting on
    whitespace-delimited words never cuts a glossary placeholder (e.g.
    "Xac") in half, since a placeholder is always exactly one such
    token."""
    words = text.split()
    return [" ".join(words[i:i + CHUNK_WORDS]) + "."
           for i in range(0, len(words), CHUNK_WORDS)]


def _prefer_chunked(original: str, chunked: str, source_protected: str,
                    glossary_map: dict[str, tuple[str, str]] | None) -> bool:
    """True if the chunked-retry candidate should replace the original
    one-shot candidate for a flagged run-on span. Primary signal is
    glossary.entity_occurrence_report() -- the same "did we keep the
    content" check recover_dropped_entities() already uses -- since a
    protected entity's occurrence count is the one thing this codebase
    can verify automatically about Turkish semantic preservation. Falls
    back to translation_qc's own "suspiciously short vs. source length"
    ratio (0.25) only when the span mentions no protected entity at all
    (the common case -- ordinary dialogue with no named character), since
    that leaves no entity signal to compare. Ties -- including "no
    entities and neither trips the length cutoff" -- keep the original:
    chunk boundaries are unproven, so this never regresses silently on a
    guess."""
    if glossary_map:
        report_a = entity_occurrence_report(source_protected, original, glossary_map)
        report_b = entity_occurrence_report(source_protected, chunked, glossary_map)
        if report_a or report_b:
            missing_a = sum(max(0, s - t) for s, t in report_a.values())
            missing_b = sum(max(0, s - t) for s, t in report_b.values())
            return missing_b < missing_a
    src_len = len(source_protected)
    a_bad = src_len >= 15 and len(original) < src_len * 0.25
    b_bad = src_len >= 15 and len(chunked) < src_len * 0.25
    return a_bad and not b_bad


def _apply_chunk_retry(translations: list[str], run_on_positions: dict[int, str],
                       payload: list[str], glossary_map: dict[str, tuple[str, str]] | None,
                       translate_fn) -> list[str]:
    """For each `payload` position flagged by is_unpunctuated_run_on()
    (position -> its already-protect()-ed text), builds a fixed-size
    word-chunked retry input via _chunk(), translates it through
    `translate_fn` -- whichever dispatch (remote or local) just produced
    `translations`, so the retry reuses the same open connection /
    already-loaded model rather than re-acquiring the GPU lock or
    reloading NLLB -- and replaces translations[pos] with the chunked
    candidate when _prefer_chunked() prefers it.

    Comparison happens on RESTORED (canonical-name) text --
    entity_occurrence_report() needs real names, not raw placeholders --
    but the substitution keeps the WINNING candidate in its ORIGINAL
    protected/placeholder form, so the caller's existing uniform
    repair_corrupted_placeholders()/restore() pass over the whole
    `translations` list (translate.py's _translate_sentences(), right
    after this is called) still runs exactly once, unchanged, over
    whichever candidate won."""
    if not run_on_positions:
        return translations
    positions = list(run_on_positions)
    chunk_payload: list[str] = []
    spans: list[tuple[int, int]] = []
    for pos in positions:
        start = len(chunk_payload)
        chunk_payload.extend(_chunk(run_on_positions[pos]))
        spans.append((start, len(chunk_payload)))
    chunk_translations = translate_fn(chunk_payload)
    for pos, (start, end) in zip(positions, spans):
        candidate_b = " ".join(chunk_translations[start:end])
        source_protected = payload[pos]
        if glossary_map:
            original_restored = restore(repair_corrupted_placeholders(translations[pos], source_protected),
                                        glossary_map)
            chunked_restored = restore(repair_corrupted_placeholders(candidate_b, source_protected),
                                       glossary_map)
        else:
            original_restored, chunked_restored = translations[pos], candidate_b
        if _prefer_chunked(original_restored, chunked_restored, source_protected, glossary_map):
            translations[pos] = candidate_b
    return translations


@dataclass
class TranslationConfig:
    repo: str = NLLB_REPO
    # cuda, not cpu: revisited this session with real nvidia-smi
    # measurements during a live job (ASR + Tdarr's hardware-transcode
    # containers, the other real GPU consumer on this host, both active
    # concurrently). Mechanically safe because worker.py already wraps a
    # whole job's stream-selection/ASR/translation in ONE outer
    # gpu.gpu_lock() (see gpu.py) -- ASR and translation can never hold
    # GPU memory at the same time within a job, and translate_batch()
    # calls free_gpu() after every chunk, not just once at the end.
    # Accepted tradeoff, not hidden: this does add real, ongoing GPU
    # contention with Tdarr during every translation phase going
    # forward, previously avoided on purpose -- judged worth it for the
    # throughput win (translation was ~3 of ~49 minutes on a real
    # episode on CPU).
    #
    # IMPORTANT, learned the hard way: _generate_one_batch's OOM-halving
    # retry is NOT a complete safety net. Real failure (Love Is In The
    # Air S01E04, 2026-09-19): Tdarr's hardware-transcode containers hit
    # their own peak (4 concurrent, ~1.66GB) at the same moment
    # translation's very first batch ran, and the card was ALREADY at
    # capacity before that batch even started -- halving retries a
    # too-large CURRENT batch, but there is no smaller batch that helps
    # when the problem is zero headroom before generate() is even
    # called. That job failed outright (PIPELINE_ERROR), not a graceful
    # degradation. num_beams/batch_size below were lowered specifically
    # to leave enough real margin that Tdarr's worst case doesn't
    # exhaust the card before a batch starts, not just to reduce the
    # common-case footprint.
    device: str = "cuda"
    max_new_tokens: int = 256
    # Halved from 4 (2026-09-19, see IMPORTANT note above) -- beam count
    # scales the number of concurrently-tracked sequences linearly
    # (batch_size * num_beams), so this alone roughly halves peak
    # generation-time activation memory versus the original default.
    num_beams: int = 2
    # Lowered from 12 (2026-09-19, see IMPORTANT note above) -- combined
    # with num_beams=2, peak concurrent sequences drop from 48 (12*4) to
    # 16 (8*2), around a third of the original footprint.
    batch_size: int = 8
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


def load_tokenizer(config: TranslationConfig, src_lang_code: str):
    """The tokenizer alone -- the only language-specific part of NLLB
    (its `src_lang` setting). The model weights are language-agnostic, so
    a caller that already holds a model needs only this to translate a
    second source language, never another load_model()."""
    from transformers import AutoTokenizer
    # cache_dir + local_files_only=True, explicitly, matching asr.py's
    # explicit download_root="/models" -- without this, huggingface_hub
    # falls back to $HF_HOME/hub, which under the container's non-root
    # `subtitle` user (no home directory) is neither the pre-populated
    # cache at /models/hf nor even a writable path: real-audio validation
    # hit a bare PermissionError on os.makedirs() at the translation
    # stage, on every job regardless of source language, once this was
    # actually exercised end-to-end for the first time in this deployment.
    return AutoTokenizer.from_pretrained(config.repo, src_lang=src_lang_code,
                                         cache_dir="/models/hf", local_files_only=True)


def load_model(config: TranslationConfig, src_lang_code: str):
    """Shared by both the local worker path (translate_spans() below) and
    translate_server.py's remote server -- so a VRAM headroom check here
    covers both this project's own shared Tesla P4 host (Tdarr transcode)
    and the remote translate-server's shared RTX 3070 host (Jellyfin/Plex
    hardware transcode; see translate_server.py's own module docstring)."""
    import torch
    from transformers import AutoModelForSeq2SeqLM
    if config.device == "cuda":
        from gpu import preflight_vram_check
        preflight_vram_check()
    tok = load_tokenizer(config, src_lang_code)
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
        # clean_up_tokenization_spaces MUST be explicit: this tokenizer's
        # own default (self.clean_up_tokenization_spaces, transformers
        # 4.48) is False unless passed, which left NLLB's raw subword
        # spacing in the output -- confirmed real artifact ("That 's
        # nice .", "go .") surviving all the way into committed .en.srt
        # files, independent of the Title Case source-casing issue
        # (asr.py's hotwords fix reduced how OFTEN it fired, since
        # differently-cased input tokenizes differently, but didn't
        # remove the underlying cause).
        return tok.batch_decode(gen, skip_special_tokens=True, clean_up_tokenization_spaces=True)
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
    no visibility until it finished entirely.

    free_gpu() runs after EVERY chunk, not just once at the end -- real
    production evidence (a full episode, GPU translation, 2026-09-19):
    without this, PyTorch's caching allocator visibly grew across the
    ~51 chunks of one translation stage (5442MiB -> 7530MiB measured via
    nvidia-smi on a 7680MiB card, down to 71MiB free at the low point,
    despite every individual chunk being the same shape/size). One
    empty_cache() per chunk keeps peak usage near the early-run
    steady-state instead of climbing toward the card's ceiling by the
    end of a long episode."""
    if not sentences:
        return []
    out = []
    for i in range(0, len(sentences), batch_size):
        out.extend(_generate_one_batch(model, tok, bos, sentences[i:i + batch_size], device, config))
        if device == "cuda":
            from gpu import free_gpu
            free_gpu(device)
        if on_progress:
            on_progress(len(out), len(sentences))
    return out


# Kept in sync with build_context_spans()'s own default -- both need the
# same notion of "a real pause, not just a display convenience" (see
# BoundaryReason's docstring). Not threaded through as a shared parameter
# since nothing in this codebase currently overrides either default.
_REAL_BOUNDARIES_FOR_CONTEXT = frozenset({BoundaryReason.REAL_ACOUSTIC_GAP, BoundaryReason.UTTERANCE_END})
_AFFIX_STRIP_CHARS = ".,!?…\"'“”‘’"


def orphan_context_padding_enabled() -> bool:
    """SUBTITLE_AI_ORPHAN_CONTEXT_PADDING=off|0|false|no disables the
    isolated-single-word soft-context grounding pass in translate_spans()
    (see _pad_orphan_context()'s docstring). On by default -- unlike ASR
    hotwords (asr.hotwords_enabled(), off by default), this is safe-by-
    construction: it only ever replaces a translation with a grounded
    candidate when a strict word-for-word diff against an independently-
    translated anchor succeeds, and always falls back to today's
    unchanged translation otherwise. The toggle exists so a real
    production regression can be turned off with an env var change, not
    a code revert -- this specific area (orphan words near a real
    acoustic gap) has already been flagged once before as needing its
    own scoped investigation rather than a quick patch (see CLAUDE.md)."""
    import os
    return os.environ.get("SUBTITLE_AI_ORPHAN_CONTEXT_PADDING", "").strip().lower() not in {
        "off", "0", "false", "no"}


def _find_orphan_spans(cues: list[Segment], spans: list[list[int]]) -> dict[int, tuple[int, bool]]:
    """Maps span index -> (context_span_index, context_before) for every
    single-cue, single-word span immediately adjacent to EXACTLY ONE real
    acoustic-gap boundary.

    Real example this targets (S01E01's closing song, see CLAUDE.md's
    "Known, not fixed" note): a mid-song timestamp gap split "Her" from
    its own continuation "şey olur, her şey biter" -- together, one
    sentence ("Her şey olur, her şey biter" = "Everything happens,
    everything ends"). The gap is real -- build_context_spans() is
    correct not to MERGE across it for display purposes -- but the two
    sides are still one sentence, so the span on the gap's far side (not
    the near side) is the useful grounding context; context_before tells
    the caller which side that is: True means the adjacent span used for
    context comes immediately BEFORE this orphan (the gap is between the
    orphan and whatever came earlier), False means immediately AFTER.

    A span gap-adjacent on BOTH sides (isolated between two real gaps) is
    deliberately skipped -- which neighbor is the real continuation is
    ambiguous, and this codebase's own precedent (_prefer_chunked's
    docstring: "Ties... keep the original... never regresses silently on
    a guess") is to never guess when there's no confident signal."""
    orphans: dict[int, tuple[int, bool]] = {}
    for i, span in enumerate(spans):
        if len(span) != 1:
            continue
        cue = cues[span[0]]
        if len(cue.text.strip().split()) != 1:
            continue
        gap_before = cue.boundary_before in _REAL_BOUNDARIES_FOR_CONTEXT
        gap_after = (i + 1 < len(spans)
                    and cues[spans[i + 1][0]].boundary_before in _REAL_BOUNDARIES_FOR_CONTEXT)
        if gap_before and not gap_after and i > 0:
            orphans[i] = (i - 1, True)
        elif gap_after and not gap_before and i + 1 < len(spans):
            orphans[i] = (i + 1, False)
    return orphans


def _strip_anchor_affix(padded_translation: str, anchor_translation: str, *,
                        context_before: bool) -> str | None:
    """Strips the words of `anchor_translation` from the front
    (context_before) or back of `padded_translation`, returning just the
    residual -- the orphan word's own grounded translation. Returns None
    (caller keeps the original, ungrounded translation) unless the
    matching slice of `padded_translation` equals `anchor_translation`
    word-for-word (case/punctuation-insensitive): NLLB is not guaranteed
    to reproduce the anchor's exact wording once it has more context to
    work with, and a wrong extraction would be worse than the status quo
    -- this never guesses past an exact match."""
    padded_words = padded_translation.split()
    anchor_words = anchor_translation.split()
    if not anchor_words or len(padded_words) <= len(anchor_words):
        return None
    if context_before:
        candidate, residual = padded_words[:len(anchor_words)], padded_words[len(anchor_words):]
    else:
        candidate, residual = padded_words[-len(anchor_words):], padded_words[:-len(anchor_words)]

    def _normalize(words: list[str]) -> list[str]:
        return [w.lower().strip(_AFFIX_STRIP_CHARS) for w in words]

    if _normalize(candidate) != _normalize(anchor_words):
        return None
    residual_text = " ".join(residual).strip()
    return residual_text or None


def _pad_orphan_context(cues: list[Segment], spans: list[list[int]], result: list[str], src_lang: str,
                        glossary_map: dict[str, tuple[str, str]] | None,
                        phrase_map: dict[str, str] | None, config: TranslationConfig | None,
                        model, tok, bos, remote_url: str | None) -> list[str]:
    """Post-pass over translate_spans()'s own `result`: for each span
    _find_orphan_spans() flags, translates a short 2-sentence probe (the
    neighbor span's nearest cue alone, and that same text padded with the
    orphan word) and, when _strip_anchor_affix() can cleanly isolate the
    orphan's own portion, replaces result[i] with it. Purely additive --
    never merges spans, never changes segmentation (each span's own
    result[i] slot is the only thing ever written), and falls back to
    today's translation whenever the extraction isn't exact.

    Uses the CLOSEST SINGLE CUE of the neighbor span (not the whole
    span's joined text) as the anchor -- deliberately short and likely to
    be one clean sentence, unlike the full context span (up to
    MAX_SPAN_CUES cues) which may itself have gone through dash-line/
    multi-sentence expansion inside translate_spans() and so would not
    exactly match a single fresh probe translation.

    Reuses _translate_sentences() (not the lower-level translate_batch())
    so the probe gets the same glossary protect()/restore() and
    phrase_map treatment as every other sentence -- an orphan word or its
    anchor can itself mention a protected name. In the rare case this
    runs against the pure local-NLLB fallback (no remote translate-server
    configured, no model pre-loaded) it costs one extra model load/free
    beyond the main batch -- accepted: orphan spans are rare by design
    (real-world evidence: none found in 20 minutes of ordinary S01E01
    dialogue, concentrated instead in sung-lyric sections -- see
    CLAUDE.md), so this never fires on an ordinary job. `on_progress` is
    deliberately not threaded through here, matching _apply_chunk_retry's
    own precedent -- this is an invisible correctness refinement, not
    part of the visible translation-progress count."""
    orphans = _find_orphan_spans(cues, spans)
    if not orphans:
        return result
    probe_sentences: list[str] = []
    order: list[int] = []
    for i, (context_idx, context_before) in orphans.items():
        context_span = spans[context_idx]
        anchor_cue = cues[context_span[-1] if context_before else context_span[0]]
        orphan_word = cues[spans[i][0]].text
        padded_text = (f"{anchor_cue.text} {orphan_word}" if context_before
                      else f"{orphan_word} {anchor_cue.text}")
        probe_sentences.extend([anchor_cue.text, padded_text])
        order.append(i)
    probe_translations = _translate_sentences(probe_sentences, src_lang, glossary_map=glossary_map,
                                              phrase_map=phrase_map, config=config, model=model,
                                              tok=tok, bos=bos, remote_url=remote_url)
    result = list(result)
    for slot, i in enumerate(order):
        anchor_translation = probe_translations[slot * 2]
        padded_translation = probe_translations[slot * 2 + 1]
        _, context_before = orphans[i]
        extracted = _strip_anchor_affix(padded_translation, anchor_translation, context_before=context_before)
        if extracted:
            result[i] = extracted
    return result


def translate_spans(cues: list[Segment], spans: list[list[int]], src_lang: str,
                    glossary_map: dict[str, tuple[str, str]] | None = None,
                    phrase_map: dict[str, str] | None = None,
                    config: TranslationConfig | None = None, model=None, tok=None, bos: int = None,
                    remote_url: str | None = None, on_progress=None) -> list[str]:
    """One translated string per span, entity-protected if a glossary is
    supplied. `model`/`tok`/`bos` injectable so pipeline.py controls model
    lifecycle/GPU lock across the whole job, not this function.

    A span whose text is 2+ dash-prefixed speaker turns on separate lines
    (e.g. "- Line one.\\n- Line two.") is expanded into its per-speaker
    lines BEFORE any of the logic below, each translated independently,
    then rejoined -- see glossary.split_multi_speaker_dash_lines()'s
    docstring for the real evidence NLLB otherwise garbles or truncates
    one of the two turns. An ordinary single-speaker span expands to a
    list of exactly itself, so every span in the common case (the
    overwhelming majority of real input) goes through _translate_sentences()
    completely unchanged from before this existed.

    Each resulting line (a whole single-speaker span, or one dash-turn of
    a multi-speaker one) is further expanded by
    glossary.split_into_sentences() if it itself contains 2+ sentences --
    see that function's docstring for the real evidence NLLB silently
    drops every sentence after the first when given a multi-sentence span
    in one generate() call. An ordinary one-sentence line expands to a
    list of exactly itself. The two expansions nest (sentence -> dash-line
    -> atomic sentence) and are rejoined in the same order: sentences
    within a line with a plain space, lines within a span via
    join_multi_speaker_dash_lines().

    Finally, _pad_orphan_context() (skippable via
    orphan_context_padding_enabled()) replaces any single-cue, single-word
    span translated in complete isolation next to a real acoustic gap
    with a context-grounded candidate, when one can be extracted with
    confidence -- see that function's docstring for the real "Her"/
    "out" defect this targets."""
    sentences = [" ".join(cues[i].text for i in span) for span in spans]
    dash_groups = [split_multi_speaker_dash_lines(s) or [s] for s in sentences]
    nested = [[split_into_sentences(line) or [line] for line in group] for group in dash_groups]
    flat_sentences = [s for group in nested for line_sentences in group for s in line_sentences]

    flat_result = _translate_sentences(flat_sentences, src_lang, glossary_map=glossary_map,
                                       phrase_map=phrase_map, config=config, model=model, tok=tok,
                                       bos=bos, remote_url=remote_url, on_progress=on_progress)

    result: list[str] = []
    cursor = 0
    for group in nested:
        lines: list[str] = []
        for line_sentences in group:
            n = len(line_sentences)
            piece = flat_result[cursor:cursor + n]
            cursor += n
            lines.append(" ".join(piece))
        result.append(join_multi_speaker_dash_lines(lines) if len(lines) > 1 else lines[0])
    if orphan_context_padding_enabled():
        result = _pad_orphan_context(cues, spans, result, src_lang, glossary_map, phrase_map,
                                     config, model, tok, bos, remote_url)
    return result


def _translate_sentences(sentences: list[str], src_lang: str,
                         glossary_map: dict[str, tuple[str, str]] | None = None,
                         phrase_map: dict[str, str] | None = None,
                         config: TranslationConfig | None = None, model=None, tok=None, bos: int = None,
                         remote_url: str | None = None, on_progress=None) -> list[str]:
    """The actual translation pipeline for a flat list of sentence
    strings -- everything translate_spans() did before dash-line
    expansion existed. Split out so translate_spans() can call it once
    on the flattened (dash-expanded) sentence list without duplicating
    any of this logic.

    `remote_url`, when set, tries a remote translate-server (see
    translate_server.py / remote_translate_batch()'s docstring for the
    real benchmark motivating this) before ever touching the local GPU.
    On a RemoteTranslationError (server down, timed out, errored), falls
    straight through to the exact same local load_model()/translate_batch()
    path used when `remote_url` is None -- a media-server hiccup makes a
    job slower, never breaks it.

    `phrase_map` (glossary.build_phrase_map's output) short-circuits the
    model entirely for a sentence that exactly matches a known phrase
    (see glossary.PhraseEntry's docstring for why: a short, context-free
    utterance gives NLLB nothing to ground on and it fabricates a
    continuation). Matched sentences never go through
    protect()/translate_batch()/restore() at all -- their text is already
    the final answer.

    A second, independent short-circuit catches a sentence that entity-
    protection reduces to nothing but a placeholder plus punctuation (a
    bare name-call, e.g. "Cenk." or "Sirius!") -- see
    glossary.bare_entity_translation()'s docstring for the real evidence
    that protecting the entity is not enough on its own to stop this
    class of hallucination. Both kinds of resolved sentences are spliced
    back into the right positions among the ones that DO need the model.
    `on_progress` reports over only the sentences actually sent to the
    model, consistent with its existing meaning (progress of real
    translation work).

    A sentence glossary.is_unpunctuated_run_on() flags (a multi-clause
    ASR transcript with no sentence-ending punctuation to split on --
    see that function's docstring for the real "Moon Flood" bug this
    targets) additionally gets a bounded, content-preserving retry via
    _apply_chunk_retry(): translated once normally (unchanged), then
    again as fixed-size word chunks, keeping whichever candidate
    glossary-entity occurrence counts (or, absent any protected entity
    in the sentence, the existing length-ratio heuristic) says preserves
    more source content. Validated 2026-09-21 against 1,157 real flagged
    S01 cues that mention a protected entity: 227 (19.6%) preferred the
    chunked candidate, and a 15-cue manual sample of those showed
    consistent, real content-preservation gains with zero regressions.
    A flagged sentence with no protected entity mention gets a weaker
    (length-ratio-only) signal and usually keeps the original -- a
    disclosed limitation, not a bug."""
    config = config or TranslationConfig()

    phrase_map = phrase_map or {}
    resolved: dict[int, str] = {}
    remaining_idx: list[int] = []
    remaining_sentences: list[str] = []
    for i, s in enumerate(sentences):
        key = _phrase_key(s)
        if key in phrase_map:
            resolved[i] = phrase_map[key]
        else:
            remaining_idx.append(i)
            remaining_sentences.append(s)

    protected = ([protect(s, glossary_map) for s in remaining_sentences]
                if glossary_map else remaining_sentences)

    nllb_idx: list[int] = []
    payload: list[str] = []
    run_on_positions: dict[int, str] = {}
    for orig, i, p in zip(remaining_sentences, remaining_idx, protected):
        bare = bare_entity_translation(p, glossary_map) if glossary_map else None
        if bare is not None:
            resolved[i] = bare
        else:
            if is_unpunctuated_run_on(orig):
                run_on_positions[len(payload)] = p
            nllb_idx.append(i)
            payload.append(p)

    owns_model = model is None
    from contextlib import nullcontext
    from gpu import gpu_lock

    translations: list[str] = []
    remote_succeeded = False
    if payload and remote_url:
        try:
            translations = remote_translate_batch(remote_url, payload, src_lang,
                                                  batch_size=config.batch_size, on_progress=on_progress)
            remote_succeeded = True
            if run_on_positions:
                translations = _apply_chunk_retry(
                    translations, run_on_positions, payload, glossary_map,
                    lambda texts: remote_translate_batch(remote_url, texts, src_lang,
                                                         batch_size=config.batch_size))
        except RemoteTranslationError:
            translations = []  # fall through to the local path below

    # gpu_lock() serializes GPU contention (see gpu.py's module docstring)
    # -- holding it for a CPU-only stage (or a successful remote call)
    # protects nothing and needlessly blocks the Analyze endpoint's
    # stream-sampler (a real GPU consumer) for no protective reason.
    needs_local_work = payload and not remote_succeeded
    needs_gpu_lock = owns_model and config.device == "cuda" and needs_local_work
    with gpu_lock() if needs_gpu_lock else nullcontext():
        if needs_local_work:
            try:
                if owns_model:
                    # Construction inside the try for the same reason as
                    # asr.py: a failed load_model() must still reach `finally`.
                    model, tok, bos = load_model(config, NLLB_LANG[src_lang])
                translations = translate_batch(model, tok, bos, payload, config.device, config,
                                               batch_size=config.batch_size, on_progress=on_progress)
                if run_on_positions:
                    translations = _apply_chunk_retry(
                        translations, run_on_positions, payload, glossary_map,
                        lambda texts: translate_batch(model, tok, bos, texts, config.device, config,
                                                      batch_size=config.batch_size))
            finally:
                if owns_model:
                    del model
                    from gpu import free_gpu
                    free_gpu(config.device)
    if glossary_map:
        translations = [repair_corrupted_placeholders(t, p) for t, p in zip(translations, payload)]
        translations = [restore(t, glossary_map) for t in translations]

    result: list[str | None] = [None] * len(sentences)
    for i, t in zip(nllb_idx, translations):
        result[i] = t
    for i, t in resolved.items():
        result[i] = t
    return result
