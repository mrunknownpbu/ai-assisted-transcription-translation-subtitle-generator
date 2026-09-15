FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl nginx supervisor \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 appuser \
    && rm -f /etc/nginx/sites-enabled/default

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip "setuptools<81" wheel \
    && pip install --no-cache-dir torch==2.5.1 --index-url "${TORCH_INDEX_URL}" \
    && pip install --no-cache-dir --no-build-isolation -r requirements.txt

COPY backend/app ./app
COPY --from=frontend-build /frontend/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/supervisord.conf /etc/supervisor/conf.d/subtitleai.conf

RUN mkdir -p \
        /data/media \
        /config/subtitleai/db \
        /config/subtitleai/output \
        /config/subtitleai/models \
        /config/subtitleai/work \
        /var/cache/nginx /var/log/nginx /var/lib/nginx \
    && chown -R appuser:appuser \
        /app /data /config/subtitleai /usr/share/nginx/html \
        /var/cache/nginx /var/log/nginx /var/lib/nginx \
    && sed -i 's/^user nginx;/# user nginx;/' /etc/nginx/nginx.conf \
    && sed -i 's#pid /run/nginx.pid;#pid /tmp/nginx.pid;#' /etc/nginx/nginx.conf

USER appuser

ENV SUBTITLE_MEDIA_DIR=/data/media \
    SUBTITLE_OUTPUT_DIR=/config/subtitleai/output \
    SUBTITLE_DB_PATH=/config/subtitleai/db/subtitles.db \
    SUBTITLE_MODELS_DIR=/config/subtitleai/models \
    SUBTITLE_WORK_DIR=/config/subtitleai/work \
    PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD curl -f http://127.0.0.1:8080/api/health || exit 1

CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf"]
