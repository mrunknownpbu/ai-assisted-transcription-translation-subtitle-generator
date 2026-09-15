"""Central configuration. All tunables come from environment variables so the
same image runs identically in CPU-only, single-GPU, and multi-GPU deployments."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUBTITLE_", env_file=".env", extra="ignore")

    # --- Storage paths (mounted volumes in Docker) ---
    media_dir: Path = Path("/data/media")
    output_dir: Path = Path("/data/output")
    db_path: Path = Path("/data/db/subtitles.db")
    models_dir: Path = Path("/data/models")
    work_dir: Path = Path("/data/work")  # scratch space for extracted audio, intermediates
    # Optional read-only media library root (e.g. a shared media-server library mount).
    # Unset by default — the register-by-path/browse endpoints 404 until this is configured,
    # so a deployment with no such mount just never sees the feature.
    library_dir: Path | None = None
    # Optional read-only root for real reference subtitle archives used by the evaluation
    # harness (app/evaluation/) -- e.g. a folder of human-made translations for a show,
    # kept outside the media library since it isn't playable media. Unset by default, same
    # as library_dir: the browse endpoint and the evaluate endpoint's archive-reference
    # option just 404 until this is configured.
    reference_dir: Path | None = None
    # Optional read-only root of layered glossary YAML profiles (global -> category ->
    # series) -- see pipeline/stage8_translation/glossary_profile.py. Unset by default:
    # jobs still get automatic TVDB-cast-list glossary population with zero configuration,
    # this only adds human-curated corrections/supplements on top when present.
    glossary_profile_dir: Path | None = None

    # --- ASR engine fallback chain, in order. Each name must exist in the engine registry. ---
    asr_engine_chain: list[str] = [
        "faster_whisper",
        "openai_whisper",
        "whisper_cpp",
        "whisper_api",
        "vosk",
    ]
    # Default bumped to large-v3 (from medium): the highest-quality faster-whisper model,
    # validated in production by a sibling project across multiple languages. Still fully
    # configurable and auto-downgraded per-job on constrained hardware (see
    # hardware.recommended_whisper_model) — this only changes the out-of-box default.
    whisper_model_size: str = "large-v3"
    openai_whisper_api_key: str | None = None
    whisper_api_base_url: str = "https://api.openai.com/v1"

    # --- Translation engine chain ---
    translation_engine_chain: list[str] = ["nllb", "api_engine"]
    # Default bumped to distilled-1.3B (from distilled-600M): both real-world sibling
    # projects independently converged on and validated 1.3B in production. distilled-600M
    # remains available for lower-resource hosts by overriding this setting.
    nllb_model_name: str = "facebook/nllb-200-distilled-1.3B"
    translation_api_key: str | None = None
    # Beam search + n-gram blocking, both confirmed in production to prevent NLLB's
    # degenerate repetition-loop failure mode (an empirically-verified real case: an input
    # mentioning a word 3 times produced ~13x repetitions of an unrelated sentence instead
    # of one). 4 is the largest (most conservative) n-gram size confirmed to fully collapse
    # that loop while leaving legitimate short source repetition untouched.
    nllb_no_repeat_ngram_size: int = 4
    # Tried 6 (up from 4) via the translation-accuracy evaluation harness on a real
    # episode: chrF +0.6pt, word-F1 flat, coverage -3.2pt -- within run-to-run ASR noise,
    # not a real gain, for more GPU compute per job. Reverted; kept at the validated value.
    nllb_num_beams: int = 4

    # --- Hallucination defense thresholds (tuned for false-negative minimization: err
    # toward keeping marginal dialogue rather than dropping it) ---
    min_word_confidence: float = 0.15
    repetition_loop_min_repeats: int = 4
    repetition_loop_min_phrase_words: int = 2
    hallucination_compression_ratio_threshold: float = 2.4
    hallucination_no_speech_prob_threshold: float = 0.6
    hallucination_low_avg_logprob_threshold: float = -1.0
    hallucination_suppression_score_threshold: float = 0.75
    hallucination_signatures_path: str | None = None  # None => bundled default_signatures.json

    # --- Target segmentation / QC ---
    max_chars_per_line: int = 42
    max_lines_per_cue: int = 2
    max_reading_cps: float = 21.0  # characters per second, common subtitling guideline
    min_cue_duration_s: float = 0.8
    chunk_pause_gap_s: float = 0.7  # source word-timestamp gap that signals a semantic break
    # Upper bound on a single source chunk before translation, so a long dialogue run with
    # short pauses can't silently exceed the translation engine's practical input length —
    # oversized chunks split into multiple SourceChunks instead of being truncated.
    max_chunk_chars: int = 300
    max_chunk_segments: int = 6

    # --- Concurrency / resource limits ---
    max_concurrent_gpu_jobs: int = 1  # per detected GPU; multiplied by gpu_count at runtime
    max_concurrent_cpu_jobs: int = 2
    job_max_retries: int = 2

    # --- Live processing (architecture-ready, disabled by default) ---
    enable_live_sources: bool = False

    def ensure_dirs(self) -> None:
        for d in (self.media_dir, self.output_dir, self.db_path.parent, self.models_dir, self.work_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
