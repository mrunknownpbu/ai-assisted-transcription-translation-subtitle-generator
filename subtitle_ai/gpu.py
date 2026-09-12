"""GPU memory lifecycle. Confirmed necessary by a real CUDA OOM (a 5-minute
real-audio test): `del model` alone does not return VRAM to the OS/other
allocators -- PyTorch's caching allocator holds "freed" memory in its own
reserved pool until explicitly told to release it. Without this, NLLB
loading right after Whisper on an 8GB card can fail even though Whisper's
Python object is already gone.

gpu_lock() addresses the sibling problem: two SEPARATE processes (two
worker containers, or a worker plus this process's own stream-sampling
endpoint) each loading a full model at the same time. Confirmed necessary
by a real concurrency test: two jobs submitted at once to an 8GB RTX 3070
both hit "CUDA failed with error out of memory" loading large-v3
simultaneously. flock on a file under the shared /cache mount serializes
GPU-heavy sections across every process that mounts that same cache path
-- not just threads within one process -- turning concurrent jobs into
safe queuing instead of a crash.

Reentrant per-thread: a single job (worker.py) acquires gpu_lock() ONCE
for its entire pipeline.run() duration (stream selection through
translation), but pipeline.py's own stages (audio_streams.recommend_stream,
asr.transcribe, translate.translate_spans) each also acquire it
internally when they own their model. Without reentrancy those inner
acquisitions would self-deadlock against the outer one on the SAME
thread. Real bug this closes: with only per-stage locking (no outer
wrap), a job releases the lock BETWEEN stream-selection/ASR/translation
-- each stage frees its own model before releasing -- and a concurrent
Analyze click's recommend_stream() call can land in exactly that gap.
Because the Analyze endpoint's sampler is a CACHED model (api.py's
get_stream_sampler(), owns_sampler=False), recommend_stream() does not
free it when the lock releases -- it stays resident in VRAM, positioned
to collide with the job's NEXT stage's model construction. A fixed
idle-timeout or headroom pre-check would only reduce how often a click
happens to land in one of these gaps; holding one outer lock for the
whole job removes the gaps entirely. Cross-thread/cross-process mutual
exclusion is unaffected: reentrancy only bypasses re-locking for the
SAME thread that already holds it -- a different thread (or process)
requesting the lock still blocks on the real flock until the holder's
outermost gpu_lock() exits.
"""

from __future__ import annotations

import fcntl
import gc
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

# Defaults under the OS temp dir -- always writable, no dependency on a
# /cache mount existing (e.g. in unit tests run outside any container) --
# so this only provides same-process/same-host locking unless a
# deployment explicitly overrides it. Real deployments that share GPU
# access across containers must set this to a path under a mount those
# containers actually share (e.g. /cache), or concurrent processes won't
# serialize against each other at all.
_LOCK_PATH = os.environ.get("SUBTITLE_AI_GPU_LOCK",
                            os.path.join(tempfile.gettempdir(), "subtitle-ai-gpu.lock"))


def free_gpu(device: str = "cuda") -> None:
    gc.collect()
    if device == "cuda":
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


# Bookkeeping for gpu_lock()'s reentrancy -- per-process (each process has
# its own module state), guarded by _state_lock since multiple threads in
# this process may probe/update it concurrently. The real OS-level
# exclusion is still the flock below; this only lets the SAME thread
# re-enter without deadlocking on itself.
_state_lock = threading.Lock()
_holder_thread: int | None = None
_depth = 0
_fh = None


@contextmanager
def gpu_lock():
    """Hold for the full lifetime of a GPU model -- from creation through
    free_gpu() -- never just around the load call. Releasing early would
    let a second process's load land in the gap and still collide with
    the first model's still-resident memory. Reentrant for the thread
    that already holds it (see module docstring); still blocks every
    other thread/process until the outermost call releases."""
    global _holder_thread, _depth, _fh
    tid = threading.get_ident()
    with _state_lock:
        reentering = _holder_thread == tid
        if reentering:
            _depth += 1

    if reentering:
        try:
            yield
        finally:
            with _state_lock:
                _depth -= 1
        return

    path = Path(_LOCK_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    fcntl.flock(fh, fcntl.LOCK_EX)
    with _state_lock:
        _holder_thread = tid
        _depth = 1
        _fh = fh
    try:
        yield
    finally:
        with _state_lock:
            _depth -= 1
            release = _depth == 0
            if release:
                _holder_thread = None
                _fh = None
        if release:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
