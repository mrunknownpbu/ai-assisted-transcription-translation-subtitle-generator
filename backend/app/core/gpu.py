"""GPU memory hygiene between pipeline stages within one job.

Whisper (via faster-whisper's CTranslate2 runtime) and NLLB (via PyTorch/`transformers`)
manage GPU memory through two entirely separate allocators. Releasing a job's VRAM
between stages needs both: dropping each engine's own model reference (so CTranslate2
frees its pool on destruction) AND explicitly clearing PyTorch's caching allocator --
`del model` alone leaves memory resident in PyTorch's cache. This is not theoretical: a
sibling project documents a real CUDA OOM from loading NLLB immediately after Whisper on
an 8GB card, the same class of GPU this platform has been deployed on.

Critically, this must run *before* the job's GPU semaphore slot is released (see
`jobs/worker.py`'s `_with_gpu` callers), not after -- releasing the slot while a model is
still VRAM-resident lets a second job's `try_acquire_gpu_slot` succeed and start loading
its own model on top of memory this job hasn't actually freed yet, defeating the
semaphore's one-job-per-slot guarantee.
"""
from __future__ import annotations

import gc
import logging

logger = logging.getLogger("subtitle_platform.core.gpu")


def unload_engines(engines) -> None:
    for engine in engines:
        unload = getattr(engine, "unload", None)
        if callable(unload):
            try:
                unload()
            except Exception as exc:
                logger.warning("Failed to unload engine '%s' cleanly: %s", getattr(engine, "name", engine), exc)

    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
