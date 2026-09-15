"""Live job progress over WebSocket. Implemented as a short-interval DB poll rather than
a pub/sub bus — simple, correct, and sufficient at the concurrency scale a self-hosted
single-node deployment runs at; the job row is already the single source of truth the
REST endpoints read, so there is nothing a bus would keep more consistent.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db.models import Job
from app.db.session import session_scope

router = APIRouter()

_POLL_INTERVAL_S = 1.0
_TERMINAL_STATUSES = {"done", "failed", "canceled"}


@router.websocket("/ws/jobs/{job_id}")
async def job_progress_ws(websocket: WebSocket, job_id: str):
    await websocket.accept()
    try:
        last_payload = None
        while True:
            with session_scope() as session:
                job = session.get(Job, job_id)
                if job is None:
                    await websocket.send_json({"error": "job not found"})
                    break
                payload = {
                    "id": job.id, "status": job.status, "current_stage": job.current_stage,
                    "progress_pct": job.progress_pct, "error_message": job.error_message,
                }
            if payload != last_payload:
                await websocket.send_json(payload)
                last_payload = payload
            if payload["status"] in _TERMINAL_STATUSES:
                break
            await asyncio.sleep(_POLL_INTERVAL_S)
    except WebSocketDisconnect:
        pass
