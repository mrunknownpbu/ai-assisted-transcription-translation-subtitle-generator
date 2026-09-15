"""FastAPI application entrypoint. Owns process startup: hardware detection + logging,
DB schema bootstrap, GPU slot semaphore sizing, and the background job-worker threads
that actually run the pipeline (kept as threads in this same process for the default
single-container deployment; `WORKER_THREADS=0` disables them so a separate `worker`
Compose service can run `python -m app.worker_main` instead — see docker-compose.yml).
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import evaluation as evaluation_routes
from app.api.routes import hardware as hardware_routes
from app.api.routes import jobs as jobs_routes
from app.api.routes import languages as languages_routes
from app.api.routes import library as library_routes
from app.api.routes import media as media_routes
from app.api.routes import outputs as outputs_routes
from app.api.routes import references as references_routes
from app.api.routes import settings as settings_routes
from app.api.websocket import router as websocket_router
from app.config import get_settings
from app.db.models import HardwareProfileRow
from app.db.session import init_db, session_scope
from app.hardware import detect_hardware
from app.jobs.queue import claim_next_job, init_gpu_slots, mark_job_failed_or_retry, recover_orphaned_jobs
from app.jobs.worker import JobCanceledError, JobRunner
from app.jobs.queue import mark_job_done as _mark_job_done  # noqa: F401  (re-exported for worker loop clarity)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("subtitle_platform.main")

WORKER_POLL_INTERVAL_S = 2.0


def _worker_loop(worker_id: str, stop_event: threading.Event) -> None:
    settings = get_settings()
    hardware_profile = detect_hardware()
    runner = JobRunner(settings, hardware_profile, worker_id=worker_id)

    while not stop_event.is_set():
        with session_scope() as session:
            job = claim_next_job(session, worker_id)
        if job is None:
            time.sleep(WORKER_POLL_INTERVAL_S)
            continue

        logger.info("[%s] claimed job %s", worker_id, job.id)
        try:
            with session_scope() as session:
                runner.run(session, job.id)
            with session_scope() as session:
                from app.jobs.queue import mark_job_done
                mark_job_done(session, job.id)
            logger.info("[%s] job %s completed", worker_id, job.id)
        except JobCanceledError:
            logger.info("[%s] job %s was canceled mid-run", worker_id, job.id)
        except Exception as exc:
            logger.exception("[%s] job %s failed: %s", worker_id, job.id, exc)
            with session_scope() as session:
                mark_job_failed_or_retry(session, job.id, str(exc))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    init_db()

    profile = detect_hardware()
    logger.info("Hardware profile: %s", profile.as_dict())

    with session_scope() as session:
        session.add(HardwareProfileRow(
            vendor=profile.vendor.value, gpu_count=profile.gpu_count,
            gpu_names_json=[g.name for g in profile.gpus], total_vram_mb=profile.total_vram_mb,
            cpu_cores=profile.cpu_cores, total_ram_mb=profile.total_ram_mb,
            fallback_reason=profile.fallback_reason, process_role="api",
        ))
        total_gpu_slots = max(profile.gpu_count, 0) * settings.max_concurrent_gpu_jobs
        if total_gpu_slots > 0:
            init_gpu_slots(session, total_gpu_slots)

        # Must run before any worker thread starts claiming jobs below: a job still marked
        # "running" from a previous process has no thread executing it anymore, and would
        # otherwise wedge its GPU slot forever (see recover_orphaned_jobs's docstring).
        orphaned = recover_orphaned_jobs(session)
        if orphaned:
            logger.warning("Recovered %d orphaned job(s) from a previous process: %s", len(orphaned), orphaned)

    stop_event: threading.Event = app.state.worker_stop_event
    worker_count = int(os.environ.get("SUBTITLE_WORKER_THREADS", "1"))
    for _ in range(worker_count):
        worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        t = threading.Thread(target=_worker_loop, args=(worker_id, stop_event), daemon=True)
        t.start()
        app.state.worker_threads.append(t)
    logger.info("Started %d in-process worker thread(s)", worker_count)

    yield

    stop_event.set()


def create_app() -> FastAPI:
    app = FastAPI(title="SubtitleAI", version="0.1.0", lifespan=_lifespan)
    app.state.worker_threads = []
    app.state.worker_stop_event = threading.Event()

    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    app.include_router(media_routes.router)
    app.include_router(library_routes.router)
    app.include_router(references_routes.router)
    app.include_router(languages_routes.router)
    app.include_router(jobs_routes.router)
    app.include_router(evaluation_routes.router)
    app.include_router(outputs_routes.router)
    app.include_router(hardware_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(websocket_router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
