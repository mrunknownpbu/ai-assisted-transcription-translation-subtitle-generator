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
import logging
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


_logger = logging.getLogger(__name__)


def _vram_margin_from_env(default: float = 3.2) -> float:
    """SUBTITLE_AI_VRAM_MARGIN_GB, tolerant of a blank or unparseable
    value. Blank matters because compose.yml forwards this as
    `${SUBTITLE_AI_VRAM_MARGIN_GB:-}` (the same "empty = default"
    convention its other optional settings use), so an unset .env entry
    arrives as "" -- a bare float("") here would crash the app at import
    time. An unparseable or negative value falls back to the default with
    a warning rather than refusing to start: a typo in an optional tuning
    knob must never take the whole service (or the remote translate-
    server) down."""
    raw = os.environ.get("SUBTITLE_AI_VRAM_MARGIN_GB", "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("ignoring invalid SUBTITLE_AI_VRAM_MARGIN_GB=%r; using %sGB", raw, default)
        return default
    if value < 0:
        _logger.warning("ignoring negative SUBTITLE_AI_VRAM_MARGIN_GB=%r; using %sGB", raw, default)
        return default
    return value


# Default headroom before constructing a model -- matches this project's own
# measured model sizes (NLLB ~2.8GB, large-v3 ASR ~3GB) plus a buffer, not
# an arbitrary round number. Configurable per deployment (a different card
# or model mix) without a code change.
DEFAULT_VRAM_MARGIN_GB = _vram_margin_from_env()


class InsufficientVramError(RuntimeError):
    """Raised by preflight_vram_check() when free VRAM is still below the
    required margin after the whole wait window -- refusing to attempt a
    model construction that would very likely CUDA-OOM, rather than let
    ctranslate2/PyTorch's own allocator fail loudly (and non-uniformly:
    confirmed real cases left partially-allocated VRAM stuck for the rest
    of the process's life -- see free_gpu()'s and gpu_lock()'s docstrings)
    mid-load."""


def preflight_vram_check(required_gb: float = DEFAULT_VRAM_MARGIN_GB, *,
                         max_wait_seconds: float = 20.0, poll_interval_seconds: float = 2.0,
                         device: int = 0) -> None:
    """Poll `torch.cuda.mem_get_info()` until at least `required_gb` is free,
    up to `max_wait_seconds`; raise InsufficientVramError if it's still not
    enough by then. A no-op when CUDA isn't available (CPU-only CI/tests) --
    there's no VRAM to check.

    Call this from INSIDE gpu_lock(), immediately before constructing a
    model. gpu_lock() only serializes THIS project's own processes against
    each other -- it says nothing about an external GPU consumer sharing
    the same physical card. Both real deployments this project runs on have
    exactly that: Tdarr hardware transcoding on the Tesla P4 host (ASR,
    local-NLLB-fallback, and the Analyze stream sampler all run there), and
    Jellyfin/Plex hardware transcoding on the RTX 3070 host (translate_
    server.py's own docstring documents this explicitly). A transcode burst
    can eat headroom in the exact window between this project's own idle-
    eviction freeing memory and the next request needing it -- gpu_lock()
    alone cannot see or wait out that kind of external contention, which is
    the real, previously-unrecoverable CUDA OOM condition this closes by
    waiting it out (bounded) instead of failing on the very first check."""
    import time
    import torch
    if not torch.cuda.is_available():
        return
    required_bytes = required_gb * (1024 ** 3)
    deadline = time.monotonic() + max_wait_seconds
    while True:
        free_bytes, _total_bytes = torch.cuda.mem_get_info(device)
        if free_bytes >= required_bytes:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            free_gb = free_bytes / (1024 ** 3)
            raise InsufficientVramError(
                f"only {free_gb:.2f}GB VRAM free after waiting {max_wait_seconds:.0f}s "
                f"(need {required_gb:.2f}GB) -- refusing to load model to avoid a CUDA OOM")
        time.sleep(min(poll_interval_seconds, remaining))


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
