"""Standalone remote translation server: a thin HTTP wrapper around
translate.py's existing, already-hardened load_model()/translate_batch()
-- no new translation logic here, just dispatch. Meant to run on a
different host with a faster/free GPU than the one this project's main
process usually has available (real motivation: a 2026-09-20 benchmark
measured an RTX 3070 at ~8x the throughput of this deployment's usual
Tesla P4, same model/config/sentences -- see
translate.remote_translate_batch()'s docstring). The main process's
worker.py talks to this over HTTP (see translate.remote_translate_batch())
and falls back to local translation if this is unreachable -- this
process is never a hard dependency for the main app to function.

Unlike the per-job load/unload in translate.py's translate_spans()
(deliberate, to free VRAM for other GPU consumers like Tdarr between
jobs), this process keeps NLLB loaded across requests rather than
per-job -- but NOT forever: this GPU is shared with Jellyfin/Plex's own
hardware transcoding on the same host (confirmed real, 2026-09-20:
both containers have full NVIDIA_DRIVER_CAPABILITIES=compute,video,utility
GPU passthrough), so a permanent ~2.8GB reservation would eat into their
headroom even during the long stretches when no translation job is
running. See _unload_if_idle()'s docstring for the mitigation.
"""

from __future__ import annotations

import asyncio
import gc
import os
import threading
import time
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI
from pydantic import BaseModel

from translate import NLLB_LANG, TranslationConfig, load_model, translate_batch

# 5 minutes: long enough that a burst of retries (e.g. re-running a whole
# season back to back, as this deployment actually does) doesn't thrash
# reload/unload, short enough that the GPU goes back to Jellyfin/Plex
# promptly once a batch of jobs finishes.
IDLE_UNLOAD_SECONDS = float(os.environ.get("TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS", "300"))
_IDLE_CHECK_INTERVAL_SECONDS = 30.0

_state: dict = {"config": None, "models": {}, "last_used": None}
_lock = threading.Lock()


def _load_for(nllb_code: str):
    """Cached per NLLB language code -- the model weights themselves are
    shared/language-agnostic (see load_model()'s docstring: only the
    tokenizer's src_lang setting is language-specific), so a second
    language reuses the same underlying model object, not a full reload.
    Also the sole place `last_used` is refreshed -- a request that's
    RUNNING keeps pushing the idle deadline out; only a genuinely quiet
    gap between requests can trigger an unload."""
    with _lock:
        if nllb_code not in _state["models"]:
            _state["models"][nllb_code] = load_model(_state["config"], nllb_code)
        _state["last_used"] = time.monotonic()
        return _state["models"][nllb_code]


def _unload_if_idle() -> bool:
    """Frees every loaded model/tokenizer and the GPU memory they hold if
    nothing has used them for IDLE_UNLOAD_SECONDS. Safe against a request
    in flight: that request already holds its own Python reference to the
    model tuple from `_load_for()`, so clearing the dict here doesn't
    affect it mid-generate() -- it just means the NEXT request reloads.
    Returns whether anything was actually unloaded (for tests)."""
    with _lock:
        if not _state["models"] or _state["last_used"] is None:
            return False
        if time.monotonic() - _state["last_used"] < IDLE_UNLOAD_SECONDS:
            return False
        _state["models"].clear()
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return True


async def _idle_unload_loop():
    while True:
        await asyncio.sleep(_IDLE_CHECK_INTERVAL_SECONDS)
        _unload_if_idle()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _state["config"] = TranslationConfig()
    # Warm the common case at boot so the first real request doesn't pay
    # model-load latency -- configurable since this deployment isn't
    # exclusively Turkish (see translate.NLLB_LANG for the full list).
    default_lang = os.environ.get("TRANSLATE_SERVER_DEFAULT_LANG", "tr")
    if default_lang in NLLB_LANG:
        _load_for(NLLB_LANG[default_lang])
    task = asyncio.create_task(_idle_unload_loop())
    yield
    task.cancel()
    _state["models"].clear()


app = FastAPI(title="Subtitle AI Translate Server", docs_url=None, redoc_url=None, lifespan=_lifespan)


class TranslateRequest(BaseModel):
    sentences: list[str]
    src_lang: str  # subtitle-ai's own 2-3 letter code, e.g. "tr" -- resolved via NLLB_LANG


@app.post("/translate")
def translate(request: TranslateRequest) -> dict:
    nllb_code = NLLB_LANG[request.src_lang]
    model, tok, bos = _load_for(nllb_code)
    config = _state["config"]
    translations = translate_batch(model, tok, bos, request.sentences, config.device, config,
                                   batch_size=config.batch_size)
    return {"translations": translations}


@app.get("/health")
def health() -> dict:
    """An empty `loaded_languages` here is a NORMAL, expected state after
    an idle-unload, not a failure -- the next /translate call reloads
    transparently, just slower than usual for that one request."""
    device = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    return {"status": "ok", "device": device, "loaded_languages": list(_state["models"])}
