"""Monotonic N:M alignment between a generated English SRT and a human
reference SRT, jointly scored on timing overlap and text similarity.

Pure text/timing logic -- no GPU, no model loading, fully deterministic
and unit-testable (see test_reference_aligner.py), the same way srt.py's
parsing is. Never fed back into the pipeline: this is regression/QA
tooling only, never a transcription or translation input (see media.py's
audio-first guarantee, which this module has no bearing on).

Design: a bounded edit-distance DP over CUE BLOCKS rather than single
cues -- the classic Needleman-Wunsch recurrence extended so a "match"
transition can consume up to MAX_MERGE consecutive cues on either side at
once. That gives genuine 1:1, 1:N, N:1 and N:M correspondence without the
combinatorial blowup of considering every possible grouping; a pure
"insert" or "delete" transition (one cue, zero cost siblings) handles
content with no counterpart at all on the other side.

The match cost combines lexical similarity (dominant) and timing overlap
(secondary): text similarity finds WHICH content corresponds across two
independently-timed subtitle tracks (dub pacing drift is real and
expected -- see the Vishwanath and Sons validation, where a Hindi dub and
an English reference differed by many seconds on the same line); timing
is then used again, separately, during classification to flag when a
textual match's timing has drifted far enough to be worth a
TIMING_DIFFERENCE call rather than silently accepting it as CORRECT.

LEXICAL SIMILARITY, BY DEFAULT -- word-token identity via difflib, no
GPU, no loaded model. align()/_classify() with no `semantic_fn` behave
exactly as originally shipped: fully deterministic, unit-testable without
a GPU (see test_reference_aligner.py's 15 synthetic cases, which exercise
exactly this path). The DP alignment SEARCH always stays lexical-only,
regardless of `semantic_fn` -- see below for why.

Re-validating against the real Hindi/Malayalam Vishwanath and Sons data
found the real limit of lexical-only comparison: genuinely correct,
fluent translations that simply choose different words than the human
reference for the same meaning ("get a medical degree, join a hospital"
vs "finish studying medicine, join the highest-paying hospital") score as
SUBSTITUTION or SEGMENTATION_DIFFERENCE, not CORRECT, because no token
overlaps at all -- no amount of stemming or synonym-list patching closes
a gap that's genuinely about world knowledge ("medical degree" and
"studying medicine" share no root, no synonym-dictionary entry would
plausibly link them), not spelling.

OPTIONAL SEMANTIC LAYER -- pass `semantic_fn: Callable[[str, str], float]
| None` to align() to ADD a semantic-similarity signal on top of the
lexical one, never in place of it: classification uses
max(lexical_similarity, semantic_similarity), so a strong lexical match
is never weakened by a noisy embedding score, while a paraphrase lexical
alone would miss can still be recognized. `nllb_semantic_similarity()`
below builds a real one, reusing this deployment's already-cached
NLLB-200 encoder (no new model download) to embed each side (mean-pooled,
masked encoder hidden states) and compare by cosine similarity -- genuine
semantic similarity, not a lexical proxy dressed up.

Why semantic scoring is a SEPARATE PASS, not part of the DP's inner loop:
the DP considers O(N*M*MAX_MERGE**2) candidate blocks (tens of thousands
even for a 12-minute clip); a neural embedding call per candidate would
make real-clip alignment intractable. Lexical similarity alone is cheap
enough to drive the search, and in practice still finds the right
correspondence even for paraphrases -- proper nouns, numbers and common
words tend to survive translation choices even when the surrounding
wording doesn't (see the "join...hospital" example above: 2 tokens still
overlap). So align() runs the existing fast lexical DP unchanged to
DECIDE the alignment, then -- only if `semantic_fn` was given -- computes
semantic similarity ONCE per already-decided block (order N+M calls, not
N*M) purely to REFINE that block's final category.

`nllb_semantic_similarity()` requires a GPU-capable environment with
NLLB-200 already cached at /models/hf and the CUDA GPU lock (gpu.py) --
it is never imported or called by align() itself or by any test in
test_reference_aligner.py; a caller must construct and pass it
explicitly. Without it (the default, `semantic_fn=None`), behavior,
performance and test requirements are unchanged from the lexical-only
version -- this module still has zero GPU/network dependency unless a
caller deliberately opts in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Callable


class AlignmentCategory(str, Enum):
    CORRECT = "CORRECT"
    SEGMENTATION_DIFFERENCE = "SEGMENTATION_DIFFERENCE"
    NORMALIZATION_DIFFERENCE = "NORMALIZATION_DIFFERENCE"
    TIMING_DIFFERENCE = "TIMING_DIFFERENCE"
    DROPPED_UTTERANCE = "DROPPED_UTTERANCE"
    SUBSTITUTION = "SUBSTITUTION"
    INSERTED_CONTENT = "INSERTED_CONTENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class AlignmentResult:
    category: AlignmentCategory
    gen_cues: tuple[Cue, ...]
    ref_cues: tuple[Cue, ...]
    lexical_similarity: float
    timing_overlap: float
    gen_range: tuple[int, int]   # [start, end) indices into the input gen_cues list
    ref_range: tuple[int, int]   # [start, end) indices into the input ref_cues list
    semantic_similarity: float | None = None  # None unless a semantic_fn was supplied to align()


# --- tunables -----------------------------------------------------------
# Bounding the block size keeps the DP at O(N*M*MAX_MERGE**2) instead of
# combinatorial; 5 comfortably covered every real N:M span observed during
# the Vishwanath and Sons validation (the largest real run seen there
# merged around 4-5 cues on one side against a differently-cut reference
# passage).
MAX_MERGE = 5
INSERT_COST = 0.85   # cost of a generated cue with no reference counterpart
DELETE_COST = 0.85   # cost of a reference cue with no generated counterpart
TEXT_WEIGHT = 0.65    # text similarity dominates alignment SEARCH --
TIMING_WEIGHT = 0.35  # dub pacing drift must not defeat a real text match;
                      # timing is re-examined on its own during classification.

HIGH_SIM = 0.60       # >= this and content is considered "the same utterance"
MED_SIM = 0.28        # >= this and content is considered "related" (partial cut)
TIMING_OK = 0.25       # minimum start/end-overlap ratio to call timing "fine"

# SequenceMatcher rewards partial block overlap even when a genuinely
# isolated extra/missing line sits in the middle -- "hello there friend
# UNRELATED JUNK goodbye for now" vs "hello there friend goodbye for now"
# still scores a high ratio, since both halves match perfectly, so without
# this penalty the DP would rather swallow the junk into one big
# SEGMENTATION_DIFFERENCE block than call it what it is: two clean CORRECT
# matches plus one DROPPED_UTTERANCE/INSERTED_CONTENT. The penalty makes
# unnecessary merging a last resort, chosen only when no cleaner
# combination of smaller matches + indels scores better.
MERGE_PENALTY = 0.5


def _normalize(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _words(text: str) -> tuple[str, ...]:
    return tuple(_normalize(text).split())


def _lexical_similarity(a: str, b: str) -> float:
    """Word-token sequence similarity, not character-level: comparing
    whole strings with difflib.SequenceMatcher scales badly once blocks
    merge several cues (real full-length episodes -- this project's
    actual scale, per its own "many long episodes" design goal -- have
    thousands of cues, and character-level matching_blocks() on merged
    multi-hundred-character strings was measured taking minutes on a
    single real 12-minute test clip). Comparing WORD lists instead is an
    order of magnitude fewer elements per comparison, still preserves
    order-sensitivity (unlike a plain bag-of-words Jaccard), and is
    arguably the more appropriate granularity for subtitle text anyway --
    matching whole words, not incidental shared substrings between
    unrelated words."""
    wa, wb = _words(a), _words(b)
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return SequenceMatcher(None, wa, wb, autojunk=False).ratio()


def _time_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Intersection-over-union of the two time spans -- 0 for disjoint
    spans, 1 for identical spans, robust to one span containing the other
    (unlike a plain overlap-over-shorter-span ratio, which a very long
    reference passage sitting near several short generated cues could
    otherwise dominate)."""
    lo, hi = max(a_start, b_start), min(a_end, b_end)
    inter = max(0.0, hi - lo)
    union = max(a_end, b_end) - min(a_start, b_start)
    return inter / union if union > 0 else 0.0


def _span(cues: tuple[Cue, ...]) -> tuple[float, float]:
    return min(c.start for c in cues), max(c.end for c in cues)


def _text(cues: tuple[Cue, ...]) -> str:
    return " ".join(c.text for c in cues)


def _match_cost(gen_block: tuple[Cue, ...], ref_block: tuple[Cue, ...]) -> float:
    gs, ge = _span(gen_block)
    rs, re_ = _span(ref_block)
    timing = _time_overlap(gs, ge, rs, re_)
    sim = _lexical_similarity(_text(gen_block), _text(ref_block))
    extra_cues = (len(gen_block) - 1) + (len(ref_block) - 1)
    return TIMING_WEIGHT * (1 - timing) + TEXT_WEIGHT * (1 - sim) + MERGE_PENALTY * extra_cues


def align(gen_cues: list[Cue], ref_cues: list[Cue],
         semantic_fn: Callable[[str, str], float] | None = None) -> list[AlignmentResult]:
    """Monotonic alignment: gen_cues[gi0:gi1] always precedes gen_cues[gi1:]
    in every later result, and likewise for ref_cues -- no reordering, only
    grouping, matching how dubbed/translated dialogue actually tracks a
    reference (scenes don't get reshuffled, only re-cut and re-timed).

    semantic_fn=None (the default) is the original, fully lexical
    behavior -- no GPU, no model, unchanged from before this parameter
    existed. The alignment SEARCH itself always uses lexical similarity
    only, regardless of semantic_fn (see module docstring for why);
    semantic_fn, if given, is applied once per final block purely to
    refine that block's classification."""
    n, m = len(gen_cues), len(ref_cues)
    INF = float("inf")
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    choice: list[list[tuple[int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best, best_choice = INF, None
            if i > 0:
                cand = dp[i - 1][j] + INSERT_COST
                if cand < best:
                    best, best_choice = cand, (1, 0)
            if j > 0:
                cand = dp[i][j - 1] + DELETE_COST
                if cand < best:
                    best, best_choice = cand, (0, 1)
            for a in range(1, min(MAX_MERGE, i) + 1):
                for b in range(1, min(MAX_MERGE, j) + 1):
                    cost = _match_cost(tuple(gen_cues[i - a:i]), tuple(ref_cues[j - b:j]))
                    cand = dp[i - a][j - b] + cost
                    if cand < best:
                        best, best_choice = cand, (a, b)
            dp[i][j] = best
            choice[i][j] = best_choice

    i, j = n, m
    blocks: list[tuple[int, int, int, int]] = []
    while i > 0 or j > 0:
        a, b = choice[i][j]
        blocks.append((i - a, i, j - b, j))
        i, j = i - a, j - b
    blocks.reverse()

    return [
        _classify(tuple(gen_cues[gi0:gi1]), tuple(ref_cues[rj0:rj1]), (gi0, gi1), (rj0, rj1),
                 semantic_fn)
        for gi0, gi1, rj0, rj1 in blocks
    ]


def _classify(gen_block: tuple[Cue, ...], ref_block: tuple[Cue, ...],
             gen_range: tuple[int, int], ref_range: tuple[int, int],
             semantic_fn: Callable[[str, str], float] | None = None) -> AlignmentResult:
    if not ref_block:
        return AlignmentResult(AlignmentCategory.INSERTED_CONTENT, gen_block, ref_block,
                               0.0, 0.0, gen_range, ref_range)
    if not gen_block:
        return AlignmentResult(AlignmentCategory.DROPPED_UTTERANCE, gen_block, ref_block,
                               0.0, 0.0, gen_range, ref_range)

    gs, ge = _span(gen_block)
    rs, re_ = _span(ref_block)
    timing = _time_overlap(gs, ge, rs, re_)
    gen_text, ref_text = _text(gen_block), _text(ref_block)
    sim = _lexical_similarity(gen_text, ref_text)
    exact = _normalize(gen_text) == _normalize(ref_text)
    is_1to1 = len(gen_block) == 1 and len(ref_block) == 1

    # Semantic similarity only ever RAISES the effective score used for
    # classification (max, never a blend that could pull a strong lexical
    # match down) -- see module docstring. `exact` stays lexical-only: a
    # semantic model saying two DIFFERENT strings are "equivalent" is a
    # paraphrase call (NORMALIZATION_DIFFERENCE at best), never grounds to
    # claim the text was unchanged.
    semantic_sim = semantic_fn(gen_text, ref_text) if semantic_fn is not None else None
    effective_sim = sim if semantic_sim is None else max(sim, semantic_sim)

    # Shape (1:1 vs N:M) is checked BEFORE treating a match as "CORRECT" --
    # an exact combined-text match spanning, say, one generated cue against
    # two reference cues is still a real segmentation difference (the cue
    # boundaries differ) even though not a single word was lost or changed.
    if effective_sim >= HIGH_SIM:
        if timing < TIMING_OK:
            category = AlignmentCategory.TIMING_DIFFERENCE
        elif not is_1to1:
            category = AlignmentCategory.SEGMENTATION_DIFFERENCE
        elif exact:
            category = AlignmentCategory.CORRECT
        else:
            # High similarity, right place in time, single cue each side,
            # yet not an exact normalized match. At word-token granularity
            # (see _lexical_similarity) this is a near-exact match with a
            # small number of tokens swapped -- casing/punctuation alone
            # would already have normalized to an exact match above, so
            # what's left here is genuinely a minor wording variant
            # (synonym choice, a contraction expanded/collapsed, a small
            # word inserted or dropped, OR -- when semantic_sim is what
            # cleared the threshold -- a fluent paraphrase), not a content
            # difference.
            category = AlignmentCategory.NORMALIZATION_DIFFERENCE
    elif not is_1to1 and effective_sim >= MED_SIM:
        # A genuine multi-cue block with only moderate combined similarity
        # is still most plausibly a boundary-driven partial correspondence
        # (a segmentation cut through the middle of a thought), not a
        # content substitution -- a real substitution is a same-slot,
        # same-shape utterance that says something else.
        category = AlignmentCategory.SEGMENTATION_DIFFERENCE
    elif effective_sim > 0:
        category = AlignmentCategory.SUBSTITUTION
    else:
        category = AlignmentCategory.UNKNOWN

    return AlignmentResult(category, gen_block, ref_block, sim, timing, gen_range, ref_range,
                           semantic_similarity=semantic_sim)


def parse_srt(path: str) -> list[Cue]:
    """Minimal, tolerant SRT reader for reference/QA comparison only --
    never a pipeline input (see module docstring).

    utf-8-sig, not utf-8: a plain "utf-8" codec does not strip a leading
    byte-order mark, so a BOM-prefixed file (confirmed real -- both human
    Turkish references used for the Love Is In The Air validation have
    one) leaves a stray \\ufeff glued to the first cue's index digit. That
    breaks the `^\\d+$` index-line check for exactly that one line, which
    makes the whole first cue look indexless and fall through to being
    silently dropped entirely -- found by a total parsed-cue count one
    short of the file's own last index number, not by an exception."""
    text = open(path, encoding="utf-8-sig", errors="replace").read()
    blocks = re.split(r"\n\s*\n", text.strip())
    time_re = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
    cues = []
    for block in blocks:
        lines = [l for l in block.strip().split("\n") if l.strip() != ""]
        if not lines:
            continue
        idx = 1 if re.match(r"^\d+$", lines[0].strip()) else 0
        if idx >= len(lines):
            continue
        m = time_re.search(lines[idx])
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x) for x in m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        cue_text = " ".join(lines[idx + 1:]).strip()
        cues.append(Cue(start=start, end=end, text=cue_text))
    return cues


def summarize(results: list[AlignmentResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.category.value] = counts.get(r.category.value, 0) + 1
    return counts


def nllb_semantic_similarity(repo: str = "facebook/nllb-200-distilled-1.3B",
                             device: str = "cuda") -> Callable[[str, str], float]:
    """Build a real semantic-similarity function, for callers who want to
    pass it as align()'s `semantic_fn` -- see module docstring for why
    this is a second pass, never part of the DP search itself.

    Reuses this deployment's already-cached NLLB-200 weights (the same
    ones translate.py loads for actual translation -- see its load_model())
    rather than adding a new model/dependency: both sides being compared
    here are English SRT text, so this only ever needs NLLB's ENCODER
    (get_encoder()), mean-pooled over the attention mask, compared by
    cosine similarity -- a standard, well-established way to get a
    sentence-level semantic vector out of an encoder-decoder model without
    running any decoding.

    Requires: a CUDA GPU, NLLB-200 cached at /models/hf (local_files_only,
    matching translate.py's own loading convention), and torch/transformers
    importable. NOT required by align() itself or by
    test_reference_aligner.py's 15 synthetic tests -- those never call
    this function, so the default test suite has no GPU/network
    dependency. The model loads lazily on first call and is held for the
    life of the returned closure (repeated calls, e.g. once per aligned
    block in a real regression run, do not reload it); GPU-heavy sections
    are held under gpu.gpu_lock() like every other model load in this
    codebase, so this coexists safely with a real job's ASR/translation
    if run against the same deployment."""
    state: dict = {}
    embedding_cache: dict[str, list[float]] = {}

    def _load():
        if "model" in state:
            return
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        from gpu import gpu_lock
        with gpu_lock():
            tok = AutoTokenizer.from_pretrained(repo, src_lang="eng_Latn",
                                                cache_dir="/models/hf", local_files_only=True)
            dtype = torch.float16 if device == "cuda" else torch.float32
            model = AutoModelForSeq2SeqLM.from_pretrained(repo, torch_dtype=dtype,
                                                          cache_dir="/models/hf",
                                                          local_files_only=True).to(device).eval()
        state["tok"], state["model"], state["torch"] = tok, model, torch

    def _embed(text: str) -> list[float]:
        if text in embedding_cache:
            return embedding_cache[text]
        _load()
        torch, tok, model = state["torch"], state["tok"], state["model"]
        from gpu import gpu_lock
        with gpu_lock():
            enc = tok(text or "", return_tensors="pt", truncation=True, max_length=512).to(device)
            with torch.no_grad():
                hidden = model.get_encoder()(**enc).last_hidden_state
            mask = enc["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-6)
            vec = torch.nn.functional.normalize(pooled, dim=-1)[0].float().cpu().tolist()
        embedding_cache[text] = vec
        return vec

    def similarity(text_a: str, text_b: str) -> float:
        if not text_a and not text_b:
            return 1.0
        if not text_a or not text_b:
            return 0.0
        va, vb = _embed(text_a), _embed(text_b)
        cosine = sum(x * y for x, y in zip(va, vb))
        return max(0.0, min(1.0, cosine))  # normalized vectors -> cosine in [-1,1]; clamp negatives to 0

    return similarity
