FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
RUN uv sync --no-install-project

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

COPY subtitle_ai /app

# Runs as a fixed non-root uid/gid (1000:1000) matching this host's
# operator account, which already owns the bind-mounted appdata cache
# directories -- NOT the v1 default of implicit root. /data (media root)
# is host-side world-writable (777, unRAID/linuxserver.io convention) so
# any non-root uid can write output SRTs there; /models is world-readable
# so a non-owning uid can still load weights. Only /cache (job db, work
# dir) needs the uid to actually match, since it is not world-writable.
RUN groupadd -g 1000 subtitle && \
    useradd -u 1000 -g 1000 -M -s /usr/sbin/nologin subtitle && \
    chown -R subtitle:subtitle /app
USER subtitle

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
