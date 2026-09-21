"""HTTP API. Explicit typed contracts -- QC is always `qc.<stage>.population`
/ `qc.<stage>.flagged` (see qc/types.py); nothing here makes the frontend
guess a backend field name.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

import alerting
import audio_streams
import glossary_profile
import media
import translate
import workdir
from events import EventBus
from jobstore import JobStore, JobStoreError
from media import VIDEO_EXTENSIONS
from output import (MAX_SRT_FILE_BYTES, TARGET_LANG, OutputSafetyError, resolve_media_path,
                    resolve_output_path, write_srt_atomic)

STATIC_DIR = Path(__file__).parent / "static"

_event_bus = EventBus()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # asyncio.Queue.put_nowait() (inside EventBus.publish) is only safe to
    # call from the thread running the loop it belongs to -- publish() is
    # called from worker.py's background threading.Thread, so the bus
    # needs a handle on the loop that's actually serving requests, bound
    # once uvicorn's loop is running (not importable/available earlier).
    _event_bus.bind_loop(asyncio.get_running_loop())
    yield


app = FastAPI(title="Subtitle AI v2", docs_url=None, redoc_url=None, lifespan=_lifespan)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Real gap this closes (production-readiness audit, 2026-09-21):
    every endpoint had zero access control, including delete/cancel/
    retry/glossary-write. Opt-in only -- confirmed LAN-only deployment
    makes this cheap insurance, not a hard requirement, so an unset
    SUBTITLE_AI_API_KEY (today's default everywhere this runs) makes
    this a no-op, identical to current behavior. Deliberately NOT
    applied to read-only or job-creation endpoints -- see the plan this
    implements for why the scope stops at delete/cancel/retry/
    glossary-write specifically."""
    expected = os.environ.get("SUBTITLE_AI_API_KEY")
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


def get_store() -> JobStore:
    # Overridable in tests via app.dependency_overrides is unnecessary
    # here -- api.py exposes create_app() for real wiring; this module-
    # level singleton is convenient for the default run path only.
    global _store
    return _store


def get_media_root() -> str:
    global _media_root
    return _media_root


def get_glossary_dir() -> str | None:
    global _glossary_dir
    return _glossary_dir


def get_glossary_suggestions_dir() -> str | None:
    global _glossary_suggestions_dir
    return _glossary_suggestions_dir


def get_work_root() -> str | None:
    return _work_root


def get_srt_upload_dir() -> str | None:
    global _srt_upload_dir
    return _srt_upload_dir


def get_event_bus() -> EventBus:
    return _event_bus


def get_static_dir() -> Path:
    global _static_dir
    return _static_dir


_store: JobStore | None = None
_media_root: str = "/data"
_glossary_dir: str | None = None
_glossary_suggestions_dir: str | None = None
_worker = None  # registered via register_worker() -- see its docstring
_srt_upload_dir: str | None = None
_work_root: str | None = None
_static_dir: Path = STATIC_DIR


def _on_job_changed(job_id: str) -> None:
    """Fires after every committed job-row mutation (see JobStore's
    on_change docstring). Two independent effects, neither allowed to
    break the other: push the GUI update (existing behavior), and --
    real gap closed by the production-readiness audit, 2026-09-21 -- fire
    the optional failure webhook (alerting.py) the moment a job's status
    actually becomes "failed". Safe against double-firing: finish() is
    the only path that ever sets a terminal status, and it's called
    exactly once per job's terminal transition."""
    _event_bus.publish({"type": "job_changed", "job_id": job_id})
    job = _store.get(job_id) if _store else None
    if job and job["status"] == "failed":
        alerting.notify_job_failed(job)


def register_worker(worker) -> None:
    """Lets /api/health report the worker THREAD's own liveness, not
    just the API process's -- see Worker.last_heartbeat's docstring for
    the real gap this closes. Untyped on purpose: worker.py already
    imports this module (api.py must not import worker.py back, or the
    two modules would import each other)."""
    global _worker
    _worker = worker


def create_app(db_path: str | Path, media_root: str = "/data", *,
               glossary_dir: str | None = None,
               glossary_suggestions_dir: str | None = None,
               srt_upload_dir: str | None = None,
               work_root: str | None = None,
               static_dir: str | Path | None = None) -> FastAPI:
    global _store, _media_root, _glossary_dir, _glossary_suggestions_dir, _srt_upload_dir, _work_root, _static_dir, _worker
    _store = JobStore(db_path, on_change=_on_job_changed)
    # Reset, not left over from a previous create_app() call in the same
    # process -- real risk this avoids: two tests in the same session
    # creating separate apps, where the second would otherwise silently
    # report the first app's (possibly stopped) worker's heartbeat.
    _worker = None
    _media_root = media_root
    _glossary_dir = glossary_dir
    _glossary_suggestions_dir = glossary_suggestions_dir
    _srt_upload_dir = srt_upload_dir
    _work_root = work_root
    _static_dir = Path(static_dir) if static_dir else STATIC_DIR
    return app


def _require_english_target(value: str) -> str:
    """Shared by both job-creation request models. translate.load_model()
    is hardwired to English output (see output.TARGET_LANG), so any other
    target would produce English text under a mislabeled filename."""
    if value != TARGET_LANG:
        raise ValueError(f'target_lang must be "{TARGET_LANG}"; '
                         "multi-target translation is not supported")
    return value


class JobRequest(BaseModel):
    video_path: str
    # "auto" (the default) means detect the spoken language from the audio;
    # a 2-3 letter code is an explicit manual override.
    source_lang: str = Field(default="auto", pattern=r"^(auto|[a-z]{2,3})$")
    target_lang: str = TARGET_LANG

    _check_target_lang = field_validator("target_lang")(_require_english_target)
    # None (the default) means let the pipeline recommend a stream; a
    # given index is an explicit manual override -- see audio_streams.py.
    audio_stream_index: int | None = Field(default=None, ge=0)
    overwrite_original: bool = False
    overwrite_english: bool = False


class SrtTranslationRequest(BaseModel):
    # REQUIRED, not optional: the destination is DERIVED from this (see
    # create_srt_translation_job -- resolve_output_path(), the exact same
    # function create_job() already uses for the video workflow), and
    # tvdb_id (hence which series-specific glossary applies during
    # translation) is derived from it too. There is no independent
    # destination field a client can set directly.
    video_path: str
    # Exactly one of these two must be given -- a library-relative path,
    # or the id returned by POST /api/srt-uploads. Both default to None
    # so the handler can tell "neither" from "both" apart.
    source_srt_path: str | None = None
    source_upload_id: str | None = None
    # "auto" (the default) means detect the language from the subtitle
    # text itself (see srt_translation.py) -- an explicit 2-3 letter code
    # is a manual override. Same pattern/validator as JobRequest.source_lang.
    source_lang: str = Field(default="auto", pattern=r"^(auto|[a-z]{2,3})$")
    target_lang: str = TARGET_LANG

    _check_target_lang = field_validator("target_lang")(_require_english_target)
    overwrite_english: bool = False


class RetryRequest(BaseModel):
    # None (the default) means "not specified, inherit the original job's
    # value" -- see retry_job()'s model_fields_set handling below. An
    # explicit true/false always wins over inheritance.
    overwrite_original: bool | None = None
    overwrite_english: bool | None = None
    source_lang: str | None = Field(default=None, pattern=r"^(auto|[a-z]{2,3})$")
    audio_stream_index: int | None = Field(default=None, ge=0)


@app.get("/api/health")
def health() -> dict:
    result = {"ok": True, "queue": "sqlite"}
    # Real gap this closes (production-readiness audit, 2026-09-21): a
    # wedged-but-not-crashed worker thread previously still reported the
    # API as healthy, since this endpoint only ever reflected the API
    # process itself. None here means no worker has been registered at
    # all (e.g. a bare create_app() in a test) -- distinct from a real,
    # stale heartbeat.
    if _worker is not None:
        result["worker_last_heartbeat_seconds_ago"] = time.time() - _worker.last_heartbeat
    return result


@app.get("/api/browse")
def browse(path: str = Query(""), file_type: str = Query("video", pattern=r"^(video|srt)$")) -> dict:
    """file_type="video" (the default, unchanged from before this param
    existed) lists directories + video files, for the existing video-job
    workflow. file_type="srt" lists directories + .srt files instead, for
    the SRT-translation workflow's source-file picker -- a directory is
    always listed either way so both pickers can navigate the same tree."""
    try:
        directory = resolve_media_path(get_media_root(), path, must_exist=True)
    except OutputSafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")
    root = Path(get_media_root()).resolve()
    wanted_suffix = ".srt" if file_type == "srt" else None
    entries = []
    for entry in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        try:
            resolved = resolve_media_path(root, entry, must_exist=True)
        except OutputSafetyError:
            continue  # a symlink escaping the root is silently skipped, never listed
        rel = str(resolved.relative_to(root))
        if resolved.is_dir():
            entries.append({"name": entry.name, "path": rel, "type": "directory"})
        elif resolved.is_file() and wanted_suffix is not None and resolved.suffix.lower() == wanted_suffix:
            entries.append({"name": entry.name, "path": rel, "type": "srt",
                            "size": resolved.stat().st_size})
        elif resolved.is_file() and wanted_suffix is None and resolved.suffix.lower() in VIDEO_EXTENSIONS:
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
        resolve_output_path(get_media_root(), request.video_path, TARGET_LANG)  # validates the path shape early
    except OutputSafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Real gap this closes (production-readiness audit, 2026-09-21): the
    # regex pattern alone (JobRequest.source_lang) accepts any
    # well-formed-looking code -- "xx" passes it but isn't a language
    # this deployment's NLLB build can translate. POST /api/srt-
    # translations already has this exact check (see
    # create_srt_translation_job below); this brings the video-job path
    # up to the same standard instead of letting an unsupported code
    # reach the worker/pipeline unchecked.
    if request.source_lang != "auto" and request.source_lang not in translate.NLLB_LANG:
        raise HTTPException(status_code=400,
                            detail=f"unsupported source_lang: {request.source_lang!r}")
    try:
        job = get_store().create(request.video_path, request.source_lang,
                                 target_lang=TARGET_LANG,
                                 audio_stream_index=request.audio_stream_index,
                                 overwrite_original=request.overwrite_original,
                                 overwrite_english=request.overwrite_english)
    except JobStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": job}


@app.get("/api/languages")
def list_languages() -> dict:
    """Single source of truth for source-language choices: translate.
    NLLB_LANG's own keys, not a second hardcoded list -- a language this
    deployment's NLLB build can't actually translate should never appear
    as a selectable option."""
    return {"languages": sorted(translate.NLLB_LANG.keys())}


# Shared with srt_translation.parse_and_validate()'s identical cap on a
# source_srt_path-sourced file -- see output.MAX_SRT_FILE_BYTES's
# docstring for the gap this closes (that path had no size limit at all).
MAX_SRT_UPLOAD_BYTES = MAX_SRT_FILE_BYTES


@app.post("/api/srt-uploads", status_code=201)
async def upload_srt(file: UploadFile) -> dict:
    """Real browser upload for the SRT-translation workflow. The client
    filename is NEVER trusted as a path -- it's stored only for display,
    and a fresh uuid names the file on disk. Staged under
    get_srt_upload_dir() (app-owned /cache state), never the media root --
    an uploaded file is transient input, not part of the user's library."""
    upload_dir = get_srt_upload_dir()
    if not upload_dir:
        raise HTTPException(status_code=503, detail="SRT upload is not configured")
    original_name = Path(file.filename or "").name  # strip any path components, display-only
    if not original_name.lower().endswith(".srt"):
        raise HTTPException(status_code=400, detail="uploaded file must have a .srt extension")

    chunks = []
    total = 0
    while True:
        chunk = await file.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_SRT_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="uploaded file exceeds the 2 MiB limit")
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="uploaded file is not valid UTF-8 text") from exc

    upload_id = uuid.uuid4().hex
    target = Path(upload_dir) / f"{upload_id}.srt"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_srt_atomic(target, content, allow_overwrite=False)
    return {"upload_id": upload_id, "filename": original_name}


@app.post("/api/srt-translations", status_code=201)
def create_srt_translation_job(request: SrtTranslationRequest) -> dict:
    """Original-language .srt -> English .srt, no ASR involved -- see
    srt_translation.py's module docstring. video_path is REQUIRED: the
    destination is derived from it (resolve_output_path(), the identical
    function create_job() uses for the video workflow) and so is tvdb_id
    (via JobStore.create_srt_translation() -> glossary_profile.
    find_tvdb_id()) -- there is no way to submit this job without a real
    episode association, by design (see this session's plan: an optional
    association was silently degrading translation accuracy)."""
    try:
        video = resolve_media_path(get_media_root(), request.video_path, must_exist=True)
    except OutputSafetyError as exc:
        raise HTTPException(status_code=400, detail=f"video_path: {exc}") from exc

    try:
        destination = resolve_output_path(get_media_root(), request.video_path, TARGET_LANG)
    except OutputSafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if bool(request.source_srt_path) == bool(request.source_upload_id):
        raise HTTPException(
            status_code=400,
            detail="exactly one of source_srt_path or source_upload_id must be given")

    source_is_uploaded = request.source_upload_id is not None
    if source_is_uploaded:
        upload_dir = get_srt_upload_dir()
        if not upload_dir:
            raise HTTPException(status_code=503, detail="SRT upload is not configured")
        try:
            source = resolve_media_path(upload_dir, f"{request.source_upload_id}.srt", must_exist=True)
        except OutputSafetyError as exc:
            raise HTTPException(status_code=400, detail=f"source_upload_id: {exc}") from exc
        source_root = Path(upload_dir).resolve()
    else:
        try:
            source = resolve_media_path(get_media_root(), request.source_srt_path, must_exist=True)
        except OutputSafetyError as exc:
            raise HTTPException(status_code=400, detail=f"source_srt_path: {exc}") from exc
        source_root = Path(get_media_root()).resolve()
    if source.suffix.lower() != ".srt":
        raise HTTPException(status_code=400, detail="source must be a .srt file")

    if source == destination:
        raise HTTPException(status_code=400,
                            detail="source subtitle and destination must not be identical")

    if request.source_lang != "auto" and request.source_lang not in translate.NLLB_LANG:
        raise HTTPException(status_code=400,
                            detail=f"unsupported source_lang: {request.source_lang!r}")

    media_root = Path(get_media_root()).resolve()
    try:
        job = get_store().create_srt_translation(
            str(source.relative_to(source_root)), str(destination.relative_to(media_root)),
            source_lang=request.source_lang, target_lang=TARGET_LANG,
            video_path=str(video.relative_to(media_root)), overwrite_english=request.overwrite_english,
            source_is_uploaded=source_is_uploaded)
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


def _series_title(tvdb_id: int | None) -> str | None:
    if tvdb_id is None or not get_glossary_dir():
        return None
    return glossary_profile.load_profile(get_glossary_dir(), tvdb_id=tvdb_id).title


@app.get("/api/series")
def list_series() -> dict:
    return {"series": [{**s, "title": _series_title(s["tvdb_id"])}
                       for s in get_store().list_series()]}


@app.get("/api/series/{tvdb_id}")
def series_detail(tvdb_id: int) -> dict:
    """tvdb_id-less ("Ungrouped") jobs have no series page -- they already
    surface in the flat /api/jobs listing; there is nothing series-shaped
    to show for a job with no series identity."""
    jobs = get_store().list_by_tvdb_id(tvdb_id)
    manual_entities = []
    if get_glossary_dir():
        profile = glossary_profile.load_profile(get_glossary_dir(), tvdb_id=tvdb_id)
        manual_entities = [{"canonical": e.canonical, "surface_forms": e.surface_forms}
                           for e in profile.entities]
    suggestions = []
    suggestions_dir = get_glossary_suggestions_dir()
    if suggestions_dir:
        path = Path(suggestions_dir) / f"{tvdb_id}.yaml"
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            suggestions = data.get("entities", [])
    return {"tvdb_id": tvdb_id, "title": _series_title(tvdb_id), "jobs": jobs,
            "manual_glossary": manual_entities, "auto_suggestions": suggestions}


class PromoteGlossaryEntityRequest(BaseModel):
    canonical: str = Field(min_length=1)
    aliases: list[str] = []


@app.post("/api/series/{tvdb_id}/glossary/promote", dependencies=[Depends(require_api_key)])
def promote_glossary_entity(tvdb_id: int, request: PromoteGlossaryEntityRequest) -> dict:
    """Turns a mined suggestion (or any name) into a real,
    translation-affecting protected entity -- the one deliberate human
    action auto_glossary.py's own safety framing requires (see its
    module docstring, updated 2026-09-19): a mined name is never
    auto-applied to translation on its own, only ever via this explicit,
    one-click-from-the-GUI action. Content-addressed by tvdb_id inside
    the file, never by filename (matches glossary_profile.load_profile()'s
    own rule) -- creating a brand new file when this series has no
    series-specific glossary yet is exactly as valid as updating one."""
    glossary_dir = get_glossary_dir()
    if not glossary_dir:
        raise HTTPException(status_code=503, detail="glossary directory is not configured")
    directory = Path(glossary_dir)
    directory.mkdir(parents=True, exist_ok=True)

    path = glossary_profile.find_series_glossary_path(directory, tvdb_id)
    if path is not None:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        path = directory / f"{tvdb_id}.yaml"
        data = {"tvdb_id": tvdb_id, "title": _series_title(tvdb_id), "entities": []}

    entities = data.setdefault("entities", [])
    existing = next((e for e in entities if e.get("canonical") == request.canonical), None)
    if existing is not None:
        existing["protected"] = True
        if request.aliases:
            existing["aliases"] = sorted(set(existing.get("aliases", [])) | set(request.aliases))
    else:
        entities.append({"canonical": request.canonical, "aliases": request.aliases, "protected": True})

    # Atomic tmp-then-replace -- identical pattern to
    # auto_glossary.write_suggestions()'s own write.
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)

    profile = glossary_profile.load_profile(glossary_dir, tvdb_id=tvdb_id)
    return {"manual_glossary": [{"canonical": e.canonical, "surface_forms": e.surface_forms}
                                for e in profile.entities]}


class UpdateGlossaryEntityRequest(BaseModel):
    original_canonical: str = Field(min_length=1)
    canonical: str = Field(min_length=1)
    aliases: list[str] = []


def _load_series_glossary_or_404(directory: Path, tvdb_id: int) -> tuple[Path, dict]:
    path = glossary_profile.find_series_glossary_path(directory, tvdb_id)
    if path is None:
        raise HTTPException(status_code=404,
                            detail="no series-specific glossary file for this series")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return path, data


def _write_series_glossary(path: Path, data: dict) -> None:
    # Atomic tmp-then-replace -- identical pattern to
    # promote_glossary_entity's own write.
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


@app.post("/api/series/{tvdb_id}/glossary/update", dependencies=[Depends(require_api_key)])
def update_glossary_entity(tvdb_id: int, request: UpdateGlossaryEntityRequest) -> dict:
    """Edits an EXISTING entry's canonical spelling and/or aliases.
    Scoped to the series-specific file only (never the global/Turkish
    tiers, which apply to every series) -- 404s if this series has no
    series-specific file yet, or if original_canonical isn't in it."""
    glossary_dir = get_glossary_dir()
    if not glossary_dir:
        raise HTTPException(status_code=503, detail="glossary directory is not configured")
    directory = Path(glossary_dir)
    path, data = _load_series_glossary_or_404(directory, tvdb_id)

    entities = data.setdefault("entities", [])
    entry = next((e for e in entities if e.get("canonical") == request.original_canonical), None)
    if entry is None:
        raise HTTPException(status_code=404,
                            detail=f"{request.original_canonical!r} not found in this series' glossary")
    entry["canonical"] = request.canonical
    entry["aliases"] = request.aliases

    _write_series_glossary(path, data)
    profile = glossary_profile.load_profile(glossary_dir, tvdb_id=tvdb_id)
    return {"manual_glossary": [{"canonical": e.canonical, "surface_forms": e.surface_forms}
                                for e in profile.entities]}


class DeleteGlossaryEntityRequest(BaseModel):
    canonical: str = Field(min_length=1)


@app.post("/api/series/{tvdb_id}/glossary/delete", dependencies=[Depends(require_api_key)])
def delete_glossary_entity(tvdb_id: int, request: DeleteGlossaryEntityRequest) -> dict:
    """Removes an entry from the series-specific glossary file entirely
    (un-protects it). Same scoping/404 rules as update above."""
    glossary_dir = get_glossary_dir()
    if not glossary_dir:
        raise HTTPException(status_code=503, detail="glossary directory is not configured")
    directory = Path(glossary_dir)
    path, data = _load_series_glossary_or_404(directory, tvdb_id)

    entities = data.setdefault("entities", [])
    entry = next((e for e in entities if e.get("canonical") == request.canonical), None)
    if entry is None:
        raise HTTPException(status_code=404,
                            detail=f"{request.canonical!r} not found in this series' glossary")
    entities.remove(entry)

    _write_series_glossary(path, data)
    profile = glossary_profile.load_profile(glossary_dir, tvdb_id=tvdb_id)
    return {"manual_glossary": [{"canonical": e.canonical, "surface_forms": e.surface_forms}
                                for e in profile.entities]}


@app.get("/api/events")
async def event_stream():
    """Server-Sent Events: a bare change signal (`{"type", "job_id"}`),
    never a duplicate of the job payload -- GET /api/jobs*/api/series*
    stay the only place response shape is decided. A subscriber reacts by
    refetching, not by trusting this event body as authoritative."""
    queue = get_event_bus().subscribe()

    async def gen():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            get_event_bus().unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = get_store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_api_key)])
def cancel_job(job_id: str) -> dict:
    try:
        return get_store().request_cancel(job_id)
    except JobStoreError as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/retry", status_code=201, dependencies=[Depends(require_api_key)])
def retry_job(job_id: str, request: RetryRequest = RetryRequest()) -> dict:
    # A request body that never mentions audio_stream_index carries the
    # original stream selection forward (like an omitted source_lang
    # does); an explicit `"audio_stream_index": null` forces AUTO
    # re-selection. Pydantic's model_fields_set is what makes these two
    # distinguishable -- a plain `is None` check couldn't tell them apart.
    stream_override = (request.audio_stream_index if "audio_stream_index" in request.model_fields_set
                       else "unset")
    # Same omitted-vs-explicit distinction as audio_stream_index above:
    # an overwrite flag not mentioned in the request body inherits the
    # original job's value (JobStore.retry()'s own "unset" sentinel);
    # an explicit true/false in the body always overrides it.
    overwrite_original = (request.overwrite_original
                          if "overwrite_original" in request.model_fields_set else "unset")
    overwrite_english = (request.overwrite_english
                         if "overwrite_english" in request.model_fields_set else "unset")
    try:
        job = get_store().retry(job_id, overwrite_original=overwrite_original,
                                overwrite_english=overwrite_english,
                                source_lang=request.source_lang,
                                audio_stream_index=stream_override)
    except JobStoreError as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {"job": job}


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_api_key)])
def delete_job(job_id: str) -> dict:
    """Removes the jobs-table row and the job's scratch directory (a failed
    job's is otherwise kept for a diagnostic window -- see workdir.py).
    Never touches a media file -- verified by test_api.py; the media root
    is never imported into this module at all."""
    try:
        get_store().delete(job_id)
    except JobStoreError as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    if get_work_root():
        workdir.cleanup_work_dir(get_work_root(), job_id)  # best-effort, never raises
    return {"deleted": job_id}


# --- Frontend serving -- MUST stay below every /api/* route above: a
# catch-all path matches whatever isn't matched by an earlier, more
# specific route, so registering it first would shadow the real API. ---

@app.get("/assets/{filename:path}")
def static_asset(filename: str) -> FileResponse:
    # Path-traversal guard, not a raw filesystem join -- the equivalent
    # of the old hardcoded {"app.js", "style.css"} whitelist, generalized
    # for Vite's nested, content-hashed assets/ output (unpredictable
    # filenames, so a fixed whitelist no longer applies).
    directory = (get_static_dir() / "assets").resolve()
    target = (directory / filename).resolve()
    if directory != target and directory not in target.parents:
        raise HTTPException(status_code=404)
    if not target.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(target)


@app.get("/{full_path:path}")
def spa_fallback(full_path: str) -> FileResponse:
    # Every client-side React Router route (e.g. /series/383383,
    # /jobs/<id>) serves the same index.html; react-router-dom takes
    # over from there. full_path is only ever used to reach this
    # branch -- never to build a filesystem path. A mistyped/removed
    # /api/* path must still 404, not silently return the app shell.
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    return FileResponse(get_static_dir() / "index.html")
