"""events.EventBus -- in-process pub/sub used to push-signal the GUI
instead of blind interval polling. See events.py's own docstring for why
publish() must hop onto the event loop via call_soon_threadsafe() rather
than touch subscriber queues directly (it's called from worker.py's
background threading.Thread, not from asyncio)."""

import asyncio
import threading
import unittest

from events import EventBus


class BasicPubSubTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscriber_receives_published_event(self):
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        queue = bus.subscribe()
        bus.publish({"type": "job_changed", "job_id": "abc"})
        event = await asyncio.wait_for(queue.get(), timeout=1)
        self.assertEqual(event, {"type": "job_changed", "job_id": "abc"})

    async def test_multiple_subscribers_all_receive_the_same_event(self):
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        q1, q2 = bus.subscribe(), bus.subscribe()
        bus.publish({"type": "job_changed", "job_id": "x"})
        e1 = await asyncio.wait_for(q1.get(), timeout=1)
        e2 = await asyncio.wait_for(q2.get(), timeout=1)
        self.assertEqual(e1, e2)

    async def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        queue = bus.subscribe()
        bus.unsubscribe(queue)
        bus.publish({"type": "job_changed", "job_id": "abc"})
        await asyncio.sleep(0.05)
        self.assertTrue(queue.empty())

    async def test_publish_before_bind_loop_does_not_raise(self):
        bus = EventBus()
        bus.subscribe()
        bus.publish({"type": "job_changed", "job_id": "abc"})  # must not raise

    async def test_publish_from_a_different_thread_is_delivered(self):
        # The real usage pattern: worker.py's Worker(threading.Thread)
        # calls publish() from a thread that is NOT running the asyncio
        # event loop. A naive queue.put_nowait() from that thread would
        # be unsafe; call_soon_threadsafe() is what makes this test pass.
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        queue = bus.subscribe()

        def publish_from_thread():
            bus.publish({"type": "job_changed", "job_id": "from-thread"})

        t = threading.Thread(target=publish_from_thread)
        t.start()
        t.join()
        event = await asyncio.wait_for(queue.get(), timeout=1)
        self.assertEqual(event["job_id"], "from-thread")


if __name__ == "__main__":
    unittest.main()
