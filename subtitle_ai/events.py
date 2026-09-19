"""In-process pub/sub for push-updating the GUI. Deliberately NOT a
message queue or cross-process broadcast -- this deployment runs a
single uvicorn worker process (see compose.yml: no --workers flag, one
container), so an in-memory asyncio.Queue per subscriber is sufficient
and avoids introducing Redis or any other broker for a single-process
app.

This carries only a change SIGNAL (`{"type": ..., "job_id": ...}`), never
a duplicate of the actual job payload -- GET /api/jobs*/api/series* stay
the single source of truth for shape; a subscriber reacts to a signal by
refetching, not by trusting this payload as authoritative.
"""

from __future__ import annotations

import asyncio


class EventBus:
    """publish() is called from worker.py's background `threading.Thread`
    (Worker(threading.Thread), see worker.py), never from the asyncio
    event loop -- asyncio.Queue.put_nowait() is NOT thread-safe to call
    from a different thread than the one running the loop, so publish()
    must hop onto the loop via call_soon_threadsafe() rather than touch
    subscriber queues directly. bind_loop() must be called once, from
    inside the running event loop (a FastAPI startup hook), before any
    publish() call from the worker thread can be delivered."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict) -> None:
        if self._loop is None:
            return  # no loop bound yet (e.g. in a sync unit test) -- no subscribers can exist either
        # put_nowait: a slow/stuck subscriber must never block the
        # publisher -- each subscriber queue is unbounded, so this never
        # raises QueueFull.
        for queue in list(self._subscribers):
            self._loop.call_soon_threadsafe(queue.put_nowait, event)
