"""Primary translation engine: Meta's NLLB-200, run locally via transformers. Chosen as
primary because it is fully self-hosted (no API key, no per-call cost, no data leaving
the host) and covers ~200 languages through FLORES-200 tags, matching the platform's
dynamic/any-target-language requirement without a fixed language list.

Model size is configurable; `stage4`'s hardware-aware pattern is mirrored here so a
future auto-select-by-VRAM policy can slot in without changing the call contract.
"""
from __future__ import annotations

import gc
import logging

from app.pipeline.stage8_translation.engines.base import TranslationEngineError

logger = logging.getLogger("subtitle_platform.pipeline.translation.nllb")


class NllbEngine:
    name = "nllb"

    def __init__(self, model_name: str = "facebook/nllb-200-distilled-1.3B", device: str | None = None,
                 cache_dir: str | None = None, max_new_tokens: int = 512,
                 no_repeat_ngram_size: int = 4, num_beams: int = 4):
        self.model_name = model_name
        self.device = device
        self.cache_dir = cache_dir
        self.max_new_tokens = max_new_tokens
        # Both confirmed in production (see config.py's comment) to prevent NLLB's
        # degenerate repetition-loop failure mode.
        self.no_repeat_ngram_size = no_repeat_ngram_size
        self.num_beams = num_beams
        self._model = None
        self._tokenizer = None

    @property
    def model_version(self) -> str:
        return f"nllb:{self.model_name}"

    def is_available(self) -> tuple[bool, str | None]:
        try:
            import sentencepiece  # noqa: F401
            import transformers  # noqa: F401
        except ImportError as exc:
            return False, f"transformers/sentencepiece not importable: {exc}"
        return True, None

    def unload(self) -> None:
        """PyTorch-backed model — dropping the reference lets `core/gpu.py::
        unload_engines`'s subsequent `torch.cuda.empty_cache()` actually reclaim its VRAM."""
        self._model = None
        self._tokenizer = None

    def _load(self):
        if self._model is None:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, cache_dir=self.cache_dir)
            self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name, cache_dir=self.cache_dir)
            device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
            self._model.to(device)
            self._device = device
        return self._model, self._tokenizer

    def _generate(self, inputs, target_token_id: int, max_new_tokens: int):
        import torch

        model, _ = self._model, self._tokenizer
        with torch.no_grad():
            return model.generate(
                **inputs, forced_bos_token_id=target_token_id, max_new_tokens=max_new_tokens,
                num_beams=self.num_beams, no_repeat_ngram_size=self.no_repeat_ngram_size,
            )

    def translate(self, text: str, source_flores: str, target_flores: str) -> str:
        if not text.strip():
            return ""
        try:
            import torch

            model, tokenizer = self._load()
            tokenizer.src_lang = source_flores
            inputs = tokenizer(text, return_tensors="pt", truncation=True).to(self._device)

            target_token_id = tokenizer.convert_tokens_to_ids(target_flores)
            if target_token_id is None or target_token_id == tokenizer.unk_token_id:
                raise TranslationEngineError(f"Tokenizer does not recognize target FLORES tag '{target_flores}'")

            max_new_tokens = self.max_new_tokens
            try:
                generated = self._generate(inputs, target_token_id, max_new_tokens)
            except torch.cuda.OutOfMemoryError:
                # Halving-retry rather than failing outright: a single long chunk on a
                # tight-VRAM GPU is exactly the case this recovers, and if it still fails
                # the TranslationEngineError below correctly falls through this platform's
                # multi-engine translation fallback chain to the next configured engine.
                del inputs
                gc.collect()
                torch.cuda.empty_cache()
                if max_new_tokens <= 32:
                    raise
                logger.warning("CUDA OOM translating with NLLB; retrying once with max_new_tokens halved")
                inputs = tokenizer(text, return_tensors="pt", truncation=True).to(self._device)
                generated = self._generate(inputs, target_token_id, max_new_tokens // 2)

            return tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()
        except TranslationEngineError:
            raise
        except Exception as exc:
            raise TranslationEngineError(f"NLLB translation failed ({source_flores}->{target_flores}): {exc}") from exc
