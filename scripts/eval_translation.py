#!/usr/bin/env python3
"""Score translation systems against human English subtitles.

The library keeps human English subtitles beside many episodes
(`<stem>.en.hi.srt`, SDH-style -- never read by the pipeline itself, see
output.PROTECTED_SUFFIXES). For Love Is In The Air they are timed to the
same release as the Turkish `<stem>.tr.srt`, nearly cue-for-cue, so each
Turkish cue can be paired with the human line(s) spoken over it.

Each system translates every source cue through the real Workflow B path
(srt_translation.py: one cue per span, the series glossary's entity
protection + phrase map, translate_spans(), then dropped-entity
recovery), and is scored with corpus chrF (character 1-6-grams, beta=2,
sacrebleu-style aggregation) against the paired references. A paired
bootstrap gives each system's difference from the first one a 95%
interval. Optionally writes a blind A/B review sheet of lines where the
first two systems disagree.

Run inside the app container (GPU, /models, /glossary):

    docker cp scripts/eval_translation.py subtitle-ai:/tmp/
    docker exec -e PYTHONPATH=/app subtitle-ai python /tmp/eval_translation.py \\
        --season "/data/.../Season 01" --episodes 6-10 --tvdb-id 383383 \\
        --system hf:32x2 --system ct2:32x2 --json /tmp/eval.json --ab-sheet /tmp/ab.html

A system is BACKEND:BATCHxBEAMS. BACKEND is `hf` or `ct2` (NLLB), or `opus`
(Helsinki-NLP/opus-mt-tc-big-tr-en, Turkish only), which goes through the same
span, glossary and entity-recovery code with only the model swapped.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

CHRF_ORDER, CHRF_BETA = 6, 2.0
_SDH = re.compile(r"\([^)]*\)|\[[^\]]*\]|♪|♫")


def clean_reference(text: str) -> str:
    """Drop SDH speaker labels / sound descriptions and dialogue dashes."""
    text = _SDH.sub(" ", text)
    text = re.sub(r"(^|\s)-\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _ngrams(text: str, n: int) -> Counter:
    s = text.replace(" ", "")
    return Counter(s[i:i + n] for i in range(len(s) - n + 1))


def chrf_stats(hyp: str, ref: str) -> list[tuple[int, int, int]]:
    """Per order: (matches, hyp n-grams, ref n-grams)."""
    out = []
    for n in range(1, CHRF_ORDER + 1):
        h, r = _ngrams(hyp, n), _ngrams(ref, n)
        out.append((sum((h & r).values()), sum(h.values()), sum(r.values())))
    return out


def chrf(stats: list[list[tuple[int, int, int]]]) -> float:
    precisions, recalls = [], []
    for n in range(CHRF_ORDER):
        match = sum(s[n][0] for s in stats)
        hyp = sum(s[n][1] for s in stats)
        ref = sum(s[n][2] for s in stats)
        if hyp and ref:
            precisions.append(match / hyp)
            recalls.append(match / ref)
    if not precisions:
        return 0.0
    p, r = sum(precisions) / len(precisions), sum(recalls) / len(recalls)
    if p + r == 0:
        return 0.0
    b2 = CHRF_BETA ** 2
    return 100 * (1 + b2) * p * r / (b2 * p + r)


def pair_cues(source, reference) -> list[tuple[int, str]]:
    """(source cue index, cleaned reference) for source cues whose human
    counterpart can be identified unambiguously: every reference cue that
    lies >= 50% inside this source cue, together covering >= 50% of it.
    Song lyrics (quoted in these subtitles) and pure sound descriptions
    are skipped -- their references are not translations of the cue."""
    pairs = []
    for i, cue in enumerate(source):
        dur = max(cue.end - cue.start, 1e-6)
        refs, covered = [], 0.0
        for ref in reference:
            overlap = min(cue.end, ref.end) - max(cue.start, ref.start)
            if overlap > 0 and overlap >= 0.5 * max(ref.end - ref.start, 1e-6):
                refs.append(ref.text)
                covered += overlap
        if not refs or covered < 0.5 * dur:
            continue
        raw = " ".join(refs)
        if raw.lstrip("-( ").startswith(("\"", "“", "♪")):
            continue
        cleaned = clean_reference(raw)
        if cleaned:
            pairs.append((i, cleaned))
    return pairs


def translate_episode(path: Path, lang: str, glossary_entities, glossary_phrases,
                      config, model, tok, bos) -> list[str]:
    """Workflow B's translation step (srt_translation.py), verbatim in
    shape: one cue per span, protect/phrase-map, then entity recovery."""
    import glossary as glossary_mod
    import translate
    from srt_translation import parse_and_validate

    cues = parse_and_validate(path)
    glossary_map = glossary_mod.build_glossary(glossary_entities) if glossary_entities else {}
    phrase_map = glossary_mod.build_phrase_map(glossary_phrases, lang) if glossary_phrases else {}
    protected = [glossary_mod.protect(c.text, glossary_map) for c in cues] if glossary_map else None
    out = translate.translate_spans(cues, [[i] for i in range(len(cues))], lang,
                                    glossary_map=glossary_map, phrase_map=phrase_map, config=config,
                                    model=model, tok=tok, bos=bos)
    if glossary_map:
        for i, (src, cand) in enumerate(zip(protected, out)):
            shortfall = glossary_mod.entity_occurrence_report(src, cand, glossary_map)
            if any(s > t for s, t in shortfall.values()):
                out[i] = glossary_mod.recover_dropped_entities(src, cand, glossary_map)
    return out


OPUS_REPOS = {"tr": "Helsinki-NLP/opus-mt-tc-big-tr-en"}


def load_system(backend: str, config, lang: str):
    """(model, tokenizer, bos) for translate_spans(). `opus` is a Marian
    seq2seq model: translate._generate_one_batch drives it like NLLB with no
    forced BOS token: the pipeline needs a non-None `bos`, so a placeholder is
    returned and dropped before generate()."""
    import translate
    if backend != "opus":
        return translate.load_model(config, translate.NLLB_LANG[lang])
    import torch
    from transformers import MarianMTModel, MarianTokenizer
    repo = OPUS_REPOS[lang]
    model = MarianMTModel.from_pretrained(repo, torch_dtype=torch.float16).cuda().eval()
    generate = model.generate

    def generate_without_bos(*args, forced_bos_token_id=None, **kwargs):
        return generate(*args, **kwargs)

    model.generate = generate_without_bos
    return model, MarianTokenizer.from_pretrained(repo), 0


def parse_episodes(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


def bootstrap_delta(a_stats, b_stats, rounds: int = 1000, seed: int = 13) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(a_stats)
    deltas = []
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        deltas.append(chrf([b_stats[i] for i in idx]) - chrf([a_stats[i] for i in idx]))
    deltas.sort()
    return deltas[int(0.025 * rounds)], deltas[int(0.975 * rounds)]


def write_ab_sheet(path: str, rows: list[dict], names: tuple[str, str], count: int, seed: int = 7) -> None:
    """Blind: A/B order is shuffled per row; the key is a separate file."""
    rng = random.Random(seed)
    differing = [r for r in rows if r["out"][names[0]] != r["out"][names[1]]]
    sample = rng.sample(differing, min(count, len(differing)))
    key, body = [], []
    for n, r in enumerate(sample, 1):
        flip = rng.random() < 0.5
        a, b = (names[1], names[0]) if flip else names
        key.append({"item": n, "A": a, "B": b, "episode": r["episode"], "cue": r["cue"]})
        body.append(
            f"<tr><td>{n}</td><td>{html.escape(r['source'])}</td>"
            f"<td>{html.escape(r['reference'])}</td>"
            f"<td>{html.escape(r['out'][a])}</td><td>{html.escape(r['out'][b])}</td>"
            f"<td>A / B / same</td></tr>")
    Path(path).write_text(
        "<!doctype html><meta charset=utf-8><title>Translation A/B review</title>"
        "<style>body{font:14px system-ui;margin:16px}td,th{border:1px solid #ccc;padding:6px;"
        "vertical-align:top}table{border-collapse:collapse}</style>"
        f"<p>{len(sample)} lines where the two systems differ. For each, mark which English line "
        "is the better subtitle for the Turkish source (the human reference is for context). "
        "Which system is A or B is shuffled per row; the key is in a separate file.</p>"
        "<table><tr><th>#</th><th>Turkish</th><th>Human reference</th><th>A</th><th>B</th>"
        "<th>Better</th></tr>" + "".join(body) + "</table>", encoding="utf-8")
    Path(path).with_suffix(".key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", required=True, help="folder holding <stem>SxxEyy.{tr,en.hi}.srt")
    ap.add_argument("--episodes", default="1-5", help="e.g. 6-10 or 1,3,5")
    ap.add_argument("--lang", default="tr")
    ap.add_argument("--tvdb-id", type=int)
    ap.add_argument("--glossary-dir", default="/glossary")
    ap.add_argument("--system", action="append", required=True, help="BACKEND:BATCHxBEAMS")
    ap.add_argument("--json")
    ap.add_argument("--ab-sheet", help="write a blind A/B HTML sheet (first two systems)")
    ap.add_argument("--ab-count", type=int, default=50)
    args = ap.parse_args()

    import glossary_profile
    import translate
    from gpu import free_gpu, gpu_lock
    from srt import parse

    profile = glossary_profile.load_profile(args.glossary_dir, tvdb_id=args.tvdb_id, enrich_from_tvdb=False)
    season = Path(args.season)
    episodes = []
    for number in parse_episodes(args.episodes):
        src = next(iter(season.glob(f"*E{number:02d}.{args.lang}.srt")), None)
        ref = next(iter(season.glob(f"*E{number:02d}.en.hi.srt")), None)
        if src and ref:
            episodes.append((f"E{number:02d}", src, pair_cues(parse(src), parse(ref))))
    total_pairs = sum(len(p) for _, _, p in episodes)
    print(f"{len(episodes)} episodes, {total_pairs} scored cue pairs", flush=True)

    rows: dict[tuple[str, int], dict] = {}
    for name, src, pairs in episodes:
        cues = parse(src)
        for i, ref in pairs:
            rows[(name, i)] = {"episode": name, "cue": i, "source": cues[i].text, "reference": ref, "out": {}}

    results = []
    for spec in args.system:
        backend, _, size = spec.partition(":")
        batch, beams = (int(x) for x in size.lower().split("x"))
        if backend != "opus":
            os.environ["SUBTITLE_AI_NLLB_BACKEND"] = backend
        config = translate.TranslationConfig(batch_size=batch, num_beams=beams)
        seconds = 0.0
        with gpu_lock():
            model, tok, bos = load_system(backend, config, args.lang)
            try:
                for name, src, pairs in episodes:
                    t0 = time.monotonic()
                    out = translate_episode(src, args.lang, profile.entities, profile.phrases,
                                            config, model, tok, bos)
                    seconds += time.monotonic() - t0
                    for i, _ in pairs:
                        rows[(name, i)]["out"][spec] = out[i]
            finally:
                del model
                free_gpu("cuda")
        stats = [chrf_stats(r["out"][spec], r["reference"]) for r in rows.values()]
        results.append({"system": spec, "chrf": round(chrf(stats), 2), "seconds": round(seconds, 1)})
        print(json.dumps(results[-1]), flush=True)

    base = args.system[0]
    base_stats = [chrf_stats(r["out"][base], r["reference"]) for r in rows.values()]
    for res in results[1:]:
        other = [chrf_stats(r["out"][res["system"]], r["reference"]) for r in rows.values()]
        lo, hi = bootstrap_delta(base_stats, other)
        res["delta_vs_first"] = round(res["chrf"] - results[0]["chrf"], 2)
        res["delta_95ci"] = [round(lo, 2), round(hi, 2)]
        res["lines_differing_from_first"] = sum(r["out"][res["system"]] != r["out"][base]
                                                for r in rows.values())
        print(f"{res['system']} vs {base}: {res['delta_vs_first']:+.2f} chrF, 95% CI "
              f"[{lo:+.2f}, {hi:+.2f}], {res['lines_differing_from_first']} lines differ", flush=True)

    if args.json:
        Path(args.json).write_text(json.dumps({
            "season": str(season), "episodes": [e for e, _, _ in episodes], "pairs": total_pairs,
            "metric": "corpus chrF (char 1-6, beta 2) vs cleaned .en.hi.srt references",
            "results": results}, indent=2), encoding="utf-8")
    if args.ab_sheet and len(args.system) >= 2:
        write_ab_sheet(args.ab_sheet, list(rows.values()), (args.system[0], args.system[1]), args.ab_count)
        print(f"wrote {args.ab_sheet} (+ .key.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
