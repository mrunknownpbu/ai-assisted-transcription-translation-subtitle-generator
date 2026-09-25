FROM node:22-slim AS frontend-build
WORKDIR /frontend
# package.json + package-lock.json copied (and `npm ci` run) BEFORE the
# rest of the source, so this layer only rebuilds when dependencies
# actually change, not on every source edit.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# ffmpeg/ffprobe are a static build copied in, not `apt-get install ffmpeg`:
# Debian's package drags in ~470MB of desktop/graphics/codec libraries
# (libllvm15, Mesa, Flite, libmfx, ...) that a headless audio-extraction job
# never touches. This app only runs `ffmpeg -ss -t -i ... -ac 1 -ar 16000
# -vn -sn` and `ffprobe -show_streams`, which any build supports.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*
COPY --from=mwader/static-ffmpeg:7.1.1 /ffmpeg /ffprobe /usr/local/bin/

# Created here, while /app doesn't exist yet, instead of via a `chown -R
# /app` after the uv sync/cudnn installs below -- overlayfs has to copy
# every touched file into a new layer, so a late chown over an already-
# ~6.7GB tree cost another ~5.6GB in the image for a directory that's
# read-only at runtime anyway (nothing in this app writes under /app; see
# the USER note below for what actually needs matching ownership).
RUN groupadd -g 1000 subtitle && \
    useradd -u 1000 -g 1000 -M -s /usr/sbin/nologin subtitle

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
# triton is excluded via pyproject's override-dependencies (only torch.compile
# uses it; this app never calls it). torch/include is the C++ headers for
# building extensions. The removal lives in THIS layer: deleting files in a
# later layer would leave them in the image.
RUN uv sync --no-install-project && \
    rm -rf /app/.venv/lib/python3.12/site-packages/torch/include

# ctranslate2 4.4.0 links cuDNN 8, but torch 2.5.1+cu121 hard-pins
# nvidia-cudnn-cu12 9.1.0.70 which only ships libcudnn_*.so.9. Install
# cuDNN 8 alongside it -- different sonames, so they coexist and each
# library resolves the major version it was linked against. (Retained from
# the prior Dockerfile: this is a real, verified GPU dependency conflict,
# not something the rebuild changes.)
RUN uv pip install --python /app/.venv/bin/python --no-deps \
        --target /opt/cudnn8 nvidia-cudnn-cu12==8.9.7.29 && \
    find /opt/cudnn8 -name "*.so.9*" -delete

ENV PATH="/app/.venv/bin:$PATH"
# Declared via ARG first, not left implicit: silences Docker's own
# "undefined variable" build lint on the ${LD_LIBRARY_PATH} reference
# below with zero behavior change -- this base image never sets it, so
# the default was always effectively "".
ARG LD_LIBRARY_PATH=""
ENV LD_LIBRARY_PATH="/opt/cudnn8/nvidia/cudnn/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:${LD_LIBRARY_PATH}"

COPY --chown=subtitle:subtitle subtitle_ai /app
# The React SPA build (see frontend/) -- only its compiled output lands
# here, never node_modules or the JS toolchain, so the frontend-build
# stage's size never reaches this final runtime image.
COPY --chown=subtitle:subtitle --from=frontend-build /frontend/dist /app/static

# Runs as a fixed non-root uid/gid (1000:1000, created earlier above)
# matching this host's operator account, which already owns the bind-
# mounted appdata cache directories -- NOT the v1 default of implicit
# root. /data (media root) is host-side world-writable (777, unRAID/
# linuxserver.io convention) so any non-root uid can write output SRTs
# there; /models and the uv-installed venv under /app are world-readable
# (root:root, default umask) so a non-owning uid can still load weights/
# import packages -- neither is ever written to at runtime. Only /cache
# (job db, work dir) needs the uid to actually match, since it is not
# world-writable.
USER subtitle

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
