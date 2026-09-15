# Single Dockerfile serves both the "cpu" and "nvidia" Compose profiles — only the torch
# wheel index differs, selected by --build-arg TORCH_INDEX_URL. ffmpeg here already
# supports NVENC when an NVIDIA GPU + driver is exposed to the container at runtime.
FROM python:3.12-slim

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY requirements.txt .
# openai-whisper's setup.py imports pkg_resources at build time, which pip's isolated
# build env no longer guarantees on current setuptools — pin setuptools<81 (last version
# to always vendor pkg_resources) and build against the outer env instead of an isolated one.
RUN pip install --no-cache-dir --upgrade pip "setuptools<81" wheel \
    && pip install --no-cache-dir torch==2.5.1 --index-url "${TORCH_INDEX_URL}" \
    && pip install --no-cache-dir --no-build-isolation -r requirements.txt

COPY app ./app

RUN mkdir -p /data/media /config/subtitleai/db /config/subtitleai/output \
    /config/subtitleai/models /config/subtitleai/work \
    && chown -R appuser:appuser /data /config /app

USER appuser

ENV SUBTITLE_MEDIA_DIR=/data/media \
    SUBTITLE_OUTPUT_DIR=/config/subtitleai/output \
    SUBTITLE_DB_PATH=/config/subtitleai/db/subtitles.db \
    SUBTITLE_MODELS_DIR=/config/subtitleai/models \
    SUBTITLE_WORK_DIR=/config/subtitleai/work \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
