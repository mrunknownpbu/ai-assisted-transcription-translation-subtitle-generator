#!/usr/bin/env python3
"""Pre-download the WeSpeaker speaker-embedding ONNX model (used by
subtitle_ai/turns.py's voice-based turn detector) into the host-side
models directory, same pattern as download_sample_model.py.

Run this ONCE from the host before (re)starting the subtitle-ai container,
or before setting SUBTITLE_AI_TURN_DETECTION=voice:

    python3 scripts/download_speaker_model.py

Or with a custom models directory:

    MODELS_DIR=/opt/docker/appdata/subtitle-ai/models python3 scripts/download_speaker_model.py

Public model, no Hugging Face token needed: Wespeaker/wespeaker-voxceleb-
resnet34-LM, ONNX export (~25 MB), a WeSpeaker ResNet34 speaker-embedding
model trained on VoxCeleb. Chosen specifically to avoid a pyannote/HF-token
dependency -- see subtitle_ai/turns.py's module docstring.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

MODEL_URL = ("https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/"
            "resolve/main/voxceleb_resnet34_LM.onnx")
MODEL_FILENAME = "voxceleb_resnet34_LM.onnx"


def main() -> None:
    models_dir = os.environ.get(
        "MODELS_DIR",
        os.path.join(os.environ.get("CONFIG_PATH", "/opt/docker/appdata"),
                     "subtitle-ai", "models"),
    )
    models_path = Path(models_dir)
    models_path.mkdir(parents=True, exist_ok=True)
    dest = models_path / MODEL_FILENAME

    if dest.is_file() and dest.stat().st_size > 0:
        print(f"Already present: {dest} ({dest.stat().st_size} bytes). Nothing to do.")
        return

    print(f"Downloading {MODEL_URL}")
    print(f"  -> {dest}")
    tmp = dest.with_suffix(".tmp")
    try:
        # A default urllib User-Agent gets a 401 from huggingface.co's API/
        # CDN in practice (confirmed 2026-09-28) -- a browser-shaped one
        # works and is not a workaround for anything auth-gated: this repo
        # and file are public, no token involved anywhere in this request.
        req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        print(f"ERROR: download failed: {exc}")
        sys.exit(1)

    size = tmp.stat().st_size
    if size < 1_000_000:   # the real file is ~25 MB; anything under 1 MB is an error page, not the model
        tmp.unlink(missing_ok=True)
        print(f"ERROR: downloaded file is only {size} bytes -- expected ~25 MB. Not installing it.")
        sys.exit(1)
    tmp.replace(dest)
    print(f"SUCCESS: model saved at {dest} ({size} bytes)")
    print("\nSet SUBTITLE_AI_TURN_DETECTION=voice to use it once redeployed.")


if __name__ == "__main__":
    main()
