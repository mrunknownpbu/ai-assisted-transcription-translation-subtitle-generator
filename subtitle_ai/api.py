"""HTTP API. Explicit typed contracts -- QC is always `qc.<stage>.population`
/ `qc.<stage>.flagged` (see qc/types.py); nothing here makes the frontend
guess a backend field name.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import audio_streams
import media
from jobstore import JobStore, JobStoreError
from media import VIDEO_EXTENSIONS
from output import OutputSafetyError, resolve_media_path, resolve_output_path

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {"app.js", "style.css"}

app = FastAPI(title="Subtitle AI v2", docs_url=None, redoc_url=None)


def get_store() -> JobStore:
    # Overridable in tests via app.dependency_overrides is unnecessary
    # here -- api.py exposes create_app() for real wiring; this module-
    # level singleton is convenient for the default run path only.
    global _store
    return _store


def get_media_root() -> str:
    global _media_root
    return _media_root


_store: JobStore | None = None
_media_root: str = "/data"


def create_app(db_path: str | Path, media_root: str = "/data") -> FastAPI:
    global _store, _media_root
    _store = JobStore(db_path)
    _media_root = media_root
    return app


class JobRequest(BaseModel):
    video_path: str
    # "auto" (the default) means detect the spoken language from the audio;
    # a 2-3 letter code is an explicit manual override.
    source_lang: str = Field(default="auto", pattern=r"^(auto|[a-z]{2,3})$")
    target_lang: str = Field(default="en", pattern=r"^[a-z]{2,3}$")
    # None (the default) means let the pipeline recommend a stream; a
    # given index is an explicit manual override -- see audio_streams.py.
    audio_stream_index: int | None = Field(default=None, ge=0)
    overwrite_original: bool = False
    overwrite_english: bool = False


class RetryRequest(BaseModel):
    overwrite_original: bool = False
    overwrite_english: bool = False
    source_lang: str | None = Field(default=None, pattern=r"^(auto|[a-z]{2,3})$")
    audio_stream_index: int | None = Field(default=None, ge=0)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "queue": "sqlite"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/static/{filename}")
def static_file(filename: str) -> FileResponse:
    # Whitelist, not a raw filesystem join -- a filename is never used to
    # build a path, only to select one of two known-safe files.
    if filename not in STATIC_FILES:
        raise HTTPException(status_code=404)
    return FileResponse(STATIC_DIR / filename)


@app.get("/api/browse")
def browse(path: str = Query("")) -> dict:
    try:
        directory = resolve_media_path(get_media_root(), path, must_exist=True)
    except OutputSafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")
    root = Path(get_media_root()).resolve()
    entries = []
    for entry in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        try:
            resolved = resolve_media_path(root, entry, must_exist=True)
        except OutputSafetyError:
            continue  # a symlink escaping the root is silently skipped, never listed
        rel = str(resolved.relative_to(root))
        if resolved.is_dir():
            entries.append({"name": entry.name, "path": rel, "type": "directory"})
        elif resolved.is_file() and resolved.suffix.lower() in VIDEO_EXTENSIONS:
            entries.append({"name": entry.name, "path": rel, "type": "video",
                            "size": resolved.stat().st_size})
    return {"path": str(directory.relative_to(root)) if directory != root else "", "entries": entries}


def _stream_to_dict(s: audio_streams.AudioStream) -> dict:
    return {
        "index": s.index, "codec": s.codec, "codec_long_name": s.codec_long_name,
        "channels": s.channels, "channel_layout": s.channel_layout,
        "sample_rate": s.sample_rate, "bit_rate": s.bit_rate, "duration": s.duration,
        "title": s.title, "language": s.language, "handler_name": s.handler_name,
        "default": s.default, "forced": s.forced, "hearing_impaired": s.hearing_impaired,
        "visual_impaired": s.visual_impaired, "commentary": s.commentary,
        "exclusion_reason": s.exclusion_reason,
    }


def _probe_metadata(path: Path) -> dict:
    """Fast metadata only -- full stream listing with every ffprobe-known
    field, but NO language detection (see /api/audio-streams for that,
    which is slower since it samples/decodes candidate streams)."""
    try:
        raw_streams, fmt = media.probe(path)
    except media.MediaError as exc:
        raise HTTPException(status_code=422, detail=f"metadata unavailable: {exc}") from exc
    audio = [_stream_to_dict(s) for s in audio_streams.parse_audio_streams(raw_streams)]
    return {"duration": float(fmt.get("duration") or 0), "audio_tracks": audio}


@app.get("/api/media")
def media_metadata(path: str = Query(...)) -> dict:
    try:
        file = resolve_media_path(get_media_root(), path, must_exist=True)
    except OutputSafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not file.is_file() or file.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="not a supported video")
    root = Path(get_media_root()).resolve()
    existing = []
    for candidate in file.parent.glob(f"{file.stem}.*.srt"):
        try:
            resolved = resolve_media_path(root, candidate, must_exist=True)
        except OutputSafetyError:
            continue
        existing.append(str(resolved.relative_to(root)))
    metadata = _probe_metadata(file)
    metadata.update(path=str(file.relative_to(root)), filename=file.name,
                    size=file.stat().st_size, existing_subtitles=sorted(existing))
    return metadata


_stream_sampler = None


def get_stream_sampler():
    # Lazily loaded once per process and reused -- avoids paying model-
    # load latency on every GUI click. NOT a small model: this deployment's
    # read-only /models only has large-v3 cached (see audio_streams.
    # default_sampler()'s own docstring), so this is exactly as VRAM-heavy
    # as the real ASR pass. Confirmed by direct reproduction: this cached
    # sampler plus a freshly-loaded main ASR model measured at 7743MB
    # combined on an 8GB card whose real usable capacity is ~7834MB (per
    # an actual production OOM's own error message) -- a razor-thin,
    # unacceptable margin, not a comfortable one. release_stream_sampler()
    # below is called by worker.py before a real job's own model load, so
    # a real job never has to compete with this for VRAM.
    global _stream_sampler
    if _stream_sampler is None:
        _stream_sampler = audio_streams.default_sampler()
    return _stream_sampler


def release_stream_sampler() -> None:
    """Evict the cached sampler and free its VRAM. Called by worker.py
    immediately before a real job's own GPU-heavy work starts, so the two
    never hold VRAM at the same time -- the only approach that actually
    ELIMINATES the collision (confirmed by direct reproduction: a fixed
    idle-timeout or a headroom pre-check would each only reduce its odds,
    not close it, since a job can start at any moment relative to a
    GUI click). Costs the next "Analyze" click a fresh model load --
    acceptable, bounded, and rare compared to a job silently colliding
    with it. Safe to call even if nothing is cached (no-op)."""
    global _stream_sampler
    if _stream_sampler is not None:
        _stream_sampler = None
        from gpu import free_gpu
        free_gpu()


@app.get("/api/audio-streams")
def audio_stream_recommendation(path: str = Query(...)) -> dict:
    """Slower than /api/media: samples each plausible-dialogue candidate
    stream with a short clip + a small ASR model to recommend one, rather
    than just listing container metadata. Called explicitly by the GUI,
    not on every file selection."""
    try:
        file = resolve_media_path(get_media_root(), path, must_exist=True)
    except OutputSafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not file.is_file() or file.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="not a supported video")
    work_dir = Path(tempfile.mkdtemp(prefix="stream-sample-"))
    try:
        recommendation = audio_streams.recommend_stream(file, work_dir, sampler=get_stream_sampler())
    except media.MediaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return {
        "streams": [_stream_to_dict(s) for s in recommendation.streams],
        "recommended_index": recommendation.recommended_index,
        "recommended_language": recommendation.recommended_language,
        "recommended_confidence": recommendation.recommended_confidence,
        "reason": recommendation.reason,
        "alternates": [{"index": a.stream.index, "language": a.detected_language,
                       "confidence": a.detection_confidence, "reason": a.reason}
                      for a in recommendation.alternates],
        "ranked": [{"index": c.stream.index, "language": c.detected_language,
                   "confidence": c.detection_confidence, "score": c.score, "reason": c.reason}
                  for c in recommendation.ranked],
    }


@app.post("/api/jobs", status_code=201)
def create_job(request: JobRequest) -> dict:
    try:
        resolve_output_path(get_media_root(), request.video_path, "en")  # validates the path shape early
    except OutputSafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        job = get_store().create(request.video_path, request.source_lang,
                                 target_lang=request.target_lang,
                                 audio_stream_index=request.audio_stream_index,
                                 overwrite_original=request.overwrite_original,
                                 overwrite_english=request.overwrite_english)
    except JobStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": job}


@app.get("/api/jobs")
def list_jobs(status: str | None = Query(None), limit: int = Query(50, ge=1, le=500),
             offset: int = Query(0, ge=0)) -> dict:
    jobs, total = get_store().list(status=status, limit=limit, offset=offset)
    return {"jobs": jobs, "total": total}


@app.get("/api/queue")
def queue_counts() -> dict:
    return get_store().counts()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = get_store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    try:
        return get_store().request_cancel(job_id)
    except JobStoreError as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/retry", status_code=201)
def retry_job(job_id: str, request: RetryRequest = RetryRequest()) -> dict:
    # A request body that never mentions audio_stream_index carries the
    # original stream selection forward (like an omitted source_lang
    # does); an explicit `"audio_stream_index": null` forces AUTO
    # re-selection. Pydantic's model_fields_set is what makes these two
    # distinguishable -- a plain `is None` check couldn't tell them apart.
    stream_override = (request.audio_stream_index if "audio_stream_index" in request.model_fields_set
                       else "unset")
    try:
        job = get_store().retry(job_id, overwrite_original=request.overwrite_original,
                                overwrite_english=request.overwrite_english,
                                source_lang=request.source_lang,
                                audio_stream_index=stream_override)
    except JobStoreError as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {"job": job}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    """Removes only the jobs-table row. Never touches a media file --
    verified by test_api.py; the media root is never imported into this
    module at all."""
    store = get_store()
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in ("completed", "failed", "skipped", "cancelled"):
        raise HTTPException(status_code=409, detail="cannot delete an active job")
    with store._immediate() as conn:
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return {"deleted": job_id}
