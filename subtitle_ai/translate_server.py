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
jobs), this process keeps NLLB loaded for its entire lifetime -- the
whole point is amortizing model-load time across many requests instead
of paying it every job.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI
from pydantic import BaseModel

from translate import NLLB_LANG, TranslationConfig, load_model, translate_batch

_state: dict = {"config": None, "models": {}}


def _load_for(nllb_code: str):
    """Cached per NLLB language code -- the model weights themselves are
    shared/language-agnostic (see load_model()'s docstring: only the
    tokenizer's src_lang setting is language-specific), so a second
    language reuses the same underlying model object, not a full reload."""
    if nllb_code not in _state["models"]:
        _state["models"][nllb_code] = load_model(_state["config"], nllb_code)
    return _state["models"][nllb_code]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _state["config"] = TranslationConfig()
    # Warm the common case at boot so the first real request doesn't pay
    # model-load latency -- configurable since this deployment isn't
    # exclusively Turkish (see translate.NLLB_LANG for the full list).
    default_lang = os.environ.get("TRANSLATE_SERVER_DEFAULT_LANG", "tr")
    if default_lang in NLLB_LANG:
        _load_for(NLLB_LANG[default_lang])
    yield
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
    device = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    return {"status": "ok", "device": device, "loaded_languages": list(_state["models"])}
