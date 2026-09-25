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
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from gpu import free_gpu
from translate import NLLB_LANG, TranslationConfig, load_model, load_tokenizer, translate_batch

_logger = logging.getLogger(__name__)


def _idle_unload_seconds_from_env(default: float = 120.0) -> float:
    """TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS, tolerant of a blank or
    unparseable value. compose.translate-server.yml forwards it as
    `${TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS:-}` (the repo's "empty =
    default" convention), so an unset .env entry arrives as "" -- a bare
    float("") at import time would crash the server on startup. An
    invalid or negative value falls back to the default with a warning
    rather than refusing to start: a typo in an optional tuning knob must
    never take the translate-server down. 0 is valid (unload at the next
    idle check)."""
    raw = os.environ.get("TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS", "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("ignoring invalid TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS=%r; using %ss", raw, default)
        return default
    if value < 0:
        _logger.warning("ignoring negative TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS=%r; using %ss", raw, default)
        return default
    return value


# 2 minutes: real back-to-back episode retries (this deployment's actual
# usage pattern, confirmed 2026-09-20) leave only a few seconds' gap
# between one job finishing and the next starting, comfortably under
# this -- so a full-season retry batch still never reloads mid-batch --
# while still returning the GPU to Jellyfin/Plex well within minutes of
# the last job finishing, not up to 5 minutes later.
IDLE_UNLOAD_SECONDS = _idle_unload_seconds_from_env()
_IDLE_CHECK_INTERVAL_SECONDS = 30.0

# Exactly ONE model is ever GPU-resident. NLLB's weights are language-
# agnostic (see translate.load_tokenizer()): only the tokenizer's
# `src_lang` is language-specific, so a second source language costs one
# small tokenizer, never another ~2.8GB model.
#
# Two locks, two jobs:
#   _lock        guards the fields of _state (held only briefly)
#   _infer_lock  serializes model load + inference -- a single model must
#                never run two generate() calls at once, and per-language
#                tokenizers are stateful, so requests take turns.
# `active_requests` is what eviction consults. It is incremented BEFORE a
# request waits on _infer_lock and decremented in `finally`, so a request
# that is merely queued behind another still keeps the model resident.
_state: dict = {"config": None, "model": None, "bos": None, "tokenizers": {},
                "last_used": None, "active_requests": 0}
_lock = threading.Lock()
_infer_lock = threading.Lock()


def _load_for(nllb_code: str):
    """Returns (model, tokenizer, bos) for a source language, loading the
    model on first use and only a tokenizer for each further language.
    Caller must hold _infer_lock (so loads never overlap inference or
    each other)."""
    config = _state["config"]
    if _state["model"] is None:
        model, tok, bos = load_model(config, nllb_code)
        with _lock:
            _state["model"], _state["bos"] = model, bos
            _state["tokenizers"][nllb_code] = tok
            _state["last_used"] = time.monotonic()
    elif nllb_code not in _state["tokenizers"]:
        tok = load_tokenizer(config, nllb_code)
        with _lock:
            _state["tokenizers"][nllb_code] = tok
    return _state["model"], _state["tokenizers"][nllb_code], _state["bos"]


def _unload_if_idle() -> bool:
    """Frees the model, tokenizers and the GPU memory they hold if nothing
    has used them for IDLE_UNLOAD_SECONDS AND no request is active or
    queued. The active check matters: a request still holds its own
    reference to the model, so dropping ours would free no VRAM -- and
    the next request would then load a SECOND model beside it. `last_used`
    is refreshed when a request finishes, so a long request never looks
    idle to the clock either. Returns whether anything was actually
    unloaded (for tests)."""
    with _lock:
        if _state["model"] is None or _state["last_used"] is None:
            return False
        if _state["active_requests"] > 0:
            return False
        if time.monotonic() - _state["last_used"] < IDLE_UNLOAD_SECONDS:
            return False
        _state["model"] = None
        _state["bos"] = None
        _state["tokenizers"].clear()
        config = _state["config"]
        free_gpu(config.device if config else "cpu")
        return True


async def _idle_unload_loop():
    while True:
        await asyncio.sleep(_IDLE_CHECK_INTERVAL_SECONDS)
        _unload_if_idle()


def _default_lang_from_env(default: str = "tr") -> str:
    """TRANSLATE_SERVER_DEFAULT_LANG (the source language warmed at boot),
    tolerant of a blank or unrecognised value. Unlike the numeric knobs
    this one never crashed on a blank -- but it failed SILENTLY, which is
    worse to forward: compose.translate-server.yml passes it as
    `${TRANSLATE_SERVER_DEFAULT_LANG:-}`, so an unset .env entry arrives as
    "", `"" in NLLB_LANG` is False, and the boot-time warm-up (today's
    default behavior: Turkish) would quietly stop happening, making the
    first real request pay the model-load latency. Blank/unset therefore
    means the default; a code NLLB_LANG doesn't know falls back to it with
    a warning instead of skipping the warm-up without a trace. (Warming the
    default costs at most one extra tokenizer -- the model itself is
    language-agnostic -- so falling back on a typo is harmless.)"""
    raw = os.environ.get("TRANSLATE_SERVER_DEFAULT_LANG", "").strip().lower()
    if not raw:
        return default
    if raw not in NLLB_LANG:
        _logger.warning("ignoring unrecognised TRANSLATE_SERVER_DEFAULT_LANG=%r; using %r", raw, default)
        return default
    return raw


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _state["config"] = TranslationConfig()
    # Warm the common case at boot so the first real request doesn't pay
    # model-load latency -- configurable since this deployment isn't
    # exclusively Turkish (see translate.NLLB_LANG for the full list).
    default_lang = _default_lang_from_env()
    if default_lang in NLLB_LANG:
        with _infer_lock:
            _load_for(NLLB_LANG[default_lang])
    task = asyncio.create_task(_idle_unload_loop())
    yield
    task.cancel()
    with _lock:
        _state["model"] = None
        _state["bos"] = None
        _state["tokenizers"].clear()


app = FastAPI(title="Subtitle AI Translate Server", docs_url=None, redoc_url=None, lifespan=_lifespan)


class TranslateRequest(BaseModel):
    sentences: list[str]
    src_lang: str  # subtitle-ai's own 2-3 letter code, e.g. "tr" -- resolved via NLLB_LANG


@app.post("/translate")
def translate(request: TranslateRequest) -> dict:
    nllb_code = NLLB_LANG.get(request.src_lang)
    if nllb_code is None:
        raise HTTPException(status_code=422, detail=f"unsupported src_lang: {request.src_lang!r}")
    with _lock:
        _state["active_requests"] += 1
    try:
        with _infer_lock:
            model, tok, bos = _load_for(nllb_code)
            config = _state["config"]
            translations = translate_batch(model, tok, bos, request.sentences, config.device, config,
                                           batch_size=config.batch_size)
    finally:
        with _lock:
            _state["active_requests"] -= 1
            _state["last_used"] = time.monotonic()
    return {"translations": translations}


@app.get("/health")
def health() -> dict:
    """`model_loaded: false` (and an empty `loaded_languages`) is a NORMAL,
    expected state after an idle-unload, not a failure -- the next
    /translate call reloads transparently, just slower than usual for
    that one request. `loaded_languages` lists the languages whose
    tokenizer is cached; there is only ever one model regardless."""
    device = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    return {"status": "ok", "device": device, "model_loaded": _state["model"] is not None,
            "loaded_languages": list(_state["tokenizers"])}
