#!/usr/bin/env python3
"""Pre-download the lightweight Whisper stream-sampling model into the
host-side models directory so it is available when the container mounts
/models read-only.

Run this ONCE from the host before (re)starting the subtitle-ai container:

    python3 scripts/download_sample_model.py

Or with a custom models directory:

    MODELS_DIR=/opt/docker/appdata/subtitle-ai/models python3 scripts/download_sample_model.py

By default downloads the model named by SUBTITLE_AI_SAMPLE_MODEL (same
default as the app: "small"). Override with:

    SUBTITLE_AI_SAMPLE_MODEL=base python3 scripts/download_sample_model.py

VRAM budget at runtime (Tesla P4 8 GB, int8 compute):
  faster-whisper-small  : ~200 MB VRAM  (this script's default)
  faster-whisper-base   : ~150 MB VRAM
  faster-whisper-large-v3: ~3 GB VRAM   (what the app falls back to without this)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    models_dir = os.environ.get(
        "MODELS_DIR",
        os.path.join(os.environ.get("CONFIG_PATH", "/opt/docker/appdata"),
                     "subtitle-ai", "models"),
    )
    model_name = (os.environ.get("SUBTITLE_AI_SAMPLE_MODEL", "small").strip()
                  or "small")

    models_path = Path(models_dir)
    if not models_path.exists():
        print(f"Creating models directory: {models_path}")
        models_path.mkdir(parents=True, exist_ok=True)

    # Check whether faster-whisper is importable (requires the Python venv).
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415
    except ImportError:
        print("ERROR: faster-whisper is not installed in this Python environment.")
        print("Run inside the uv venv:")
        print(f"  uv run --python /app/.venv/bin/python {__file__}")
        sys.exit(1)

    # Check whether the model is already present.
    # faster-whisper stores models under <download_root>/models--<org>--<name>/
    # (Hugging Face cache layout).  A simple prefix search is sufficient to
    # detect an existing cache entry without re-implementing HF's naming.
    slug = model_name.replace("-", "--")
    existing = list(models_path.glob(f"*{slug}*"))
    if existing:
        print(f"Model '{model_name}' already cached at: {existing[0]}")
        print("Nothing to do.")
        return

    print(f"Downloading faster-whisper '{model_name}' into: {models_path}")
    print("(This may take a minute on first run -- the model is ~150-250 MB.)")

    # Use cpu+float32 just for the download -- we only need the weights on
    # disk, not to actually run inference here.
    try:
        WhisperModel(model_name, device="cpu", compute_type="float32",
                     download_root=str(models_path))
    except Exception as exc:
        print(f"ERROR: download failed: {exc}")
        sys.exit(1)

    # Confirm the cache entry appeared.
    cached = list(models_path.glob(f"*{slug}*"))
    if cached:
        print(f"SUCCESS: model cached at {cached[0]}")
        print(f"\nThe container will now use '{model_name}' for stream sampling")
        print("(~200 MB VRAM) instead of falling back to large-v3 (~3 GB).")
    else:
        print("WARNING: download appeared to succeed but no cache directory found.")
        print(f"Check {models_path} manually.")


if __name__ == "__main__":
    main()
