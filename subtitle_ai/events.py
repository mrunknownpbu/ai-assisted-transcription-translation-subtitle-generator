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


class _CoalescingQueue(asyncio.Queue):
    """A bounded queue which retains at most one pending event per job."""

    def __init__(self, maxsize: int) -> None:
        super().__init__(maxsize=maxsize)
        self._pending_job_ids: set[str] = set()

    def put_nowait(self, event: dict) -> None:
        job_id = event.get("job_id")
        if job_id is not None and job_id in self._pending_job_ids:
            return
        if self.full():
            discarded = self._get()
            # asyncio.Queue's bookkeeping isn't in typeshed; dropping an item
            # without a task_done() call must still balance join().
            self._unfinished_tasks -= 1  # type: ignore[attr-defined]
            if self._unfinished_tasks == 0:  # type: ignore[attr-defined]
                self._finished.set()  # type: ignore[attr-defined]
            discarded_job_id = discarded.get("job_id")
            if discarded_job_id is not None:
                self._pending_job_ids.discard(discarded_job_id)
        if job_id is not None:
            self._pending_job_ids.add(job_id)
        super().put_nowait(event)

    def get_nowait(self) -> dict:
        event = super().get_nowait()
        job_id = event.get("job_id")
        if job_id is not None:
            self._pending_job_ids.discard(job_id)
        return event


class EventBus:
    """publish() is called from worker.py's background `threading.Thread`
    (Worker(threading.Thread), see worker.py), never from the asyncio
    event loop -- asyncio.Queue.put_nowait() is NOT thread-safe to call
    from a different thread than the one running the loop, so publish()
    must hop onto the loop via call_soon_threadsafe() rather than touch
    subscriber queues directly. bind_loop() must be called once, from
    inside the running event loop (a FastAPI startup hook), before any
    publish() call from the worker thread can be delivered."""

    def __init__(self, *, subscriber_queue_size: int = 100,
                 max_subscribers: int = 25) -> None:
        self._subscriber_queue_size = subscriber_queue_size
        self._max_subscribers = max_subscribers
        self._subscribers: set[_CoalescingQueue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue | None:
        if len(self._subscribers) >= self._max_subscribers:
            return None
        queue = _CoalescingQueue(self._subscriber_queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict) -> None:
        if self._loop is None:
            return  # no loop bound yet (e.g. in a sync unit test) -- no subscribers can exist either
        # put_nowait: a slow/stuck subscriber must never block the
        # publisher. Queues are bounded and coalesce pending changes for
        # each job, so a disconnected client cannot accumulate work.
        for queue in list(self._subscribers):
            self._loop.call_soon_threadsafe(queue.put_nowait, event)
