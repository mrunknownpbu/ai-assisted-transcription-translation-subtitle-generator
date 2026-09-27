#!/usr/bin/env python3
"""Benchmark NLLB translation throughput on this host's GPU.

Feeds one real subtitle file's sentences (split exactly as
translate_spans() splits them: dash lines, then sentences) through
translate.translate_batch() once per configuration, and reports
sentences/sec, peak VRAM, and how many outputs differ from the first
configuration. Runs inside the app container, where the model cache and
GPU live, and takes gpu_lock() so it never overlaps a real job:

    docker cp scripts/bench_translate.py subtitle-ai:/tmp/
    docker exec -e PYTHONPATH=/app subtitle-ai python /tmp/bench_translate.py \\
        "/data/.../S01E03.tr.srt" --lang tr --config 8x2 --config 32x2 --config 32x4

A config is BATCHxBEAMS, with an optional ":shared" suffix to also run
the per-chunk empty_cache() of the shared-GPU profile. Pass --json PATH
to save the results (commit them under benchmark-results/).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def load_sentences(path: str) -> list[str]:
    from glossary import split_into_sentences, split_multi_speaker_dash_lines
    from srt import parse
    cues = parse(path)
    out: list[str] = []
    for cue in cues:
        for line in split_multi_speaker_dash_lines(cue.text) or [cue.text]:
            out.extend(split_into_sentences(line) or [line])
    return [s for s in out if s.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("srt")
    ap.add_argument("--lang", default="tr")
    ap.add_argument("--config", action="append", required=True, help="BATCHxBEAMS[:shared]")
    ap.add_argument("--limit", type=int, default=0, help="only the first N sentences")
    ap.add_argument("--json")
    ap.add_argument("--show-diffs", type=int, default=0, help="print N outputs that differ from the first config")
    ap.add_argument("--backend", choices=["hf", "ct2"], default="hf")
    ap.add_argument("--save-outputs", help="write the first config's translations here (JSON list)")
    ap.add_argument("--compare-to", help="count/print differences against a --save-outputs file")
    args = ap.parse_args()

    os.environ["SUBTITLE_AI_NLLB_BACKEND"] = args.backend
    import torch
    from gpu import free_gpu, gpu_lock
    from translate import NLLB_LANG, TranslationConfig, load_model, translate_batch

    sentences = load_sentences(args.srt)
    if args.limit:
        sentences = sentences[:args.limit]
    print(f"{len(sentences)} sentences from {os.path.basename(args.srt)}", flush=True)

    results, baseline = [], None
    with gpu_lock():
        t0 = time.monotonic()
        model, tok, bos = load_model(TranslationConfig(), NLLB_LANG[args.lang])
        load_s = time.monotonic() - t0
        print(f"model load {load_s:.1f}s", flush=True)
        try:
            for spec in args.config:
                size, _, flag = spec.partition(":")
                batch, beams = (int(x) for x in size.lower().split("x"))
                os.environ["SUBTITLE_AI_GPU_SHARED"] = "1" if flag == "shared" else ""
                config = TranslationConfig(batch_size=batch, num_beams=beams)
                torch.cuda.reset_peak_memory_stats()
                free_before = torch.cuda.mem_get_info()[0]
                torch.cuda.synchronize()
                t0 = time.monotonic()
                out = translate_batch(model, tok, bos, sentences, "cuda", config, batch_size=batch)
                torch.cuda.synchronize()
                secs = time.monotonic() - t0
                # CTranslate2 allocates outside torch, so for ct2 report how
                # much free device memory dropped instead (lower bound).
                peak = (torch.cuda.max_memory_reserved() / 2**30 if args.backend == "hf"
                        else (free_before - torch.cuda.mem_get_info()[0]) / 2**30)
                if baseline is None:
                    baseline = out
                    if args.save_outputs:
                        with open(args.save_outputs, "w") as fh:
                            json.dump(out, fh, ensure_ascii=False)
                    if args.compare_to:
                        ref = json.load(open(args.compare_to))
                        other = [(sentences[i], ref[i], out[i]) for i in range(len(out)) if out[i] != ref[i]]
                        print(f"  vs {os.path.basename(args.compare_to)}: {len(other)}/{len(out)} differ",
                              flush=True)
                        for src, a, b in other[:args.show_diffs]:
                            print(f"  SRC {src}\n    ref:  {a}\n    this: {b}", flush=True)
                diffs = [(sentences[i], baseline[i], out[i]) for i in range(len(out)) if out[i] != baseline[i]]
                diff = len(diffs)
                for src, a, b in diffs[:args.show_diffs]:
                    print(f"  SRC {src}\n    first: {a}\n    this:  {b}", flush=True)
                row = {"config": spec, "backend": args.backend, "batch": batch, "beams": beams, "shared": flag == "shared",
                       "seconds": round(secs, 1), "sent_per_s": round(len(sentences) / secs, 2),
                       "peak_reserved_gb": round(peak, 2), "differs_from_first": diff}
                results.append(row)
                print(json.dumps(row), flush=True)
        finally:
            del model
            free_gpu("cuda")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"srt": args.srt, "sentences": len(sentences), "model_load_s": round(load_s, 1),
                       "gpu": torch.cuda.get_device_name(0), "results": results}, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
