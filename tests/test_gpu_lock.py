"""Direct reproduction of the mirror-image sampler-collision fix.

No GPU needed -- this is a pure synchronization bug: gpu.gpu_lock() itself
already worked correctly as a plain (non-reentrant) cross-process flock;
the bug was in how worker.py used it (one lock per pipeline STAGE, not one
lock for the whole job), which left gaps between stages for a concurrent
Analyze click to slip into. These tests exercise the real gpu.py module
with lightweight timed "work" standing in for real model load/inference,
proving the actual locking behavior rather than mocking it away.
"""
import threading
import time
import unittest

import gpu


class ReentrancyTests(unittest.TestCase):
    def test_same_thread_can_nest_without_deadlocking(self):
        # This is the shape worker.py now relies on: an outer gpu_lock()
        # for the whole job, with pipeline.py's own stages each also
        # acquiring gpu_lock() internally on the SAME thread.
        entered_outer = entered_inner = False

        def run():
            nonlocal entered_outer, entered_inner
            with gpu.gpu_lock():
                entered_outer = True
                with gpu.gpu_lock():
                    entered_inner = True

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "same-thread nested gpu_lock() deadlocked")
        self.assertTrue(entered_outer)
        self.assertTrue(entered_inner)

    def test_lock_state_is_fully_released_after_nested_use(self):
        with gpu.gpu_lock():
            with gpu.gpu_lock():
                pass
        self.assertIsNone(gpu._holder_thread)
        self.assertEqual(gpu._depth, 0)
        self.assertIsNone(gpu._fh)


class CrossThreadExclusionTests(unittest.TestCase):
    def test_different_threads_still_serialize_even_through_nested_calls(self):
        """Reentrancy must be thread-scoped, not a global bypass -- a
        naive depth counter with no thread-identity check would let a
        SECOND thread slip in while the first is nested, which is exactly
        the collision this whole fix exists to prevent."""
        order = []
        lock = threading.Lock()
        release_a = threading.Event()
        a_in_outer = threading.Event()

        def thread_a():
            with gpu.gpu_lock():
                with lock:
                    order.append(("a", "enter"))
                a_in_outer.set()
                with gpu.gpu_lock():   # nested, same thread -- must not release the OS lock
                    release_a.wait(timeout=5)
                with lock:
                    order.append(("a", "still-inner-exited"))
            with lock:
                order.append(("a", "exit"))

        def thread_b():
            a_in_outer.wait(timeout=5)
            time.sleep(0.05)   # give thread_a time to be deep in its nested block
            with lock:
                order.append(("b", "blocked-until-now"))
            with gpu.gpu_lock():
                with lock:
                    order.append(("b", "enter"))

        ta = threading.Thread(target=thread_a)
        tb = threading.Thread(target=thread_b)
        ta.start()
        tb.start()
        time.sleep(0.2)
        # At this point thread_b must still be blocked on the real flock --
        # its "enter" must not have happened while thread_a's nested lock
        # (same thread) is still open.
        with lock:
            self.assertNotIn(("b", "enter"), order,
                              "thread_b acquired the lock while thread_a still held it -- "
                              "reentrancy leaked across threads")
        release_a.set()
        ta.join(timeout=5)
        tb.join(timeout=5)
        self.assertFalse(ta.is_alive())
        self.assertFalse(tb.is_alive())
        # Both threads finishing at all (join didn't time out) plus the
        # pre-release assertion above is the real proof: once thread_a
        # actually releases the OS-level flock, whether thread_b's woken
        # syscall or thread_a's own remaining Python bytecode runs first
        # is an unordered race the GIL/OS scheduler is free to resolve
        # either way -- not something a correct implementation promises,
        # so asserting a fixed order between them would be testing an
        # accident of scheduling, not a real property.
        self.assertIn(("b", "enter"), order)
        self.assertIn(("a", "exit"), order)


class MirrorCollisionGapTests(unittest.TestCase):
    """Models the exact worker.py-before-the-fix shape: a 'job' that opens
    and closes gpu_lock() once per stage (stream-select, ASR, translate),
    versus the fixed shape that holds one lock for the whole job. A
    concurrent 'Analyze click' thread races to grab the lock the instant
    it's free."""

    # Event-driven, not sleep-raced: the analyze thread is woken exactly
    # when stream_select's stage-level lock use has finished, so the test
    # doesn't depend on guessing a timing window. A real per-stage
    # gpu_lock() use always has SOME nonzero gap before the next one is
    # acquired (at minimum a few bytecode instructions) -- the explicit
    # 0.05s sleep below in the unwrapped path stands in for that real gap
    # so the race is deterministic rather than relying on it being wide
    # enough by chance.
    def _run_job_and_analyze(self, *, wrap_whole_job: bool):
        stream_select_done = threading.Event()
        translate_started_at = {}
        translate_ended_at = {}
        analyze_entered_at = {}

        def run_stage(name):
            if name == "translate":
                translate_started_at["t"] = time.time()
            time.sleep(0.05)
            if name == "translate":
                translate_ended_at["t"] = time.time()

        def job():
            stages = ["stream_select", "asr", "translate"]
            if wrap_whole_job:
                with gpu.gpu_lock():
                    for stage in stages:
                        with gpu.gpu_lock():
                            run_stage(stage)
                        if stage == "stream_select":
                            stream_select_done.set()
            else:
                for stage in stages:
                    with gpu.gpu_lock():
                        run_stage(stage)
                    if stage == "stream_select":
                        stream_select_done.set()
                    time.sleep(0.05)   # the real gap: lock released between stages

        def analyze():
            stream_select_done.wait(timeout=5)
            with gpu.gpu_lock():
                analyze_entered_at["t"] = time.time()

        tj = threading.Thread(target=job)
        ta = threading.Thread(target=analyze)
        tj.start()
        ta.start()
        tj.join(timeout=5)
        ta.join(timeout=5)
        self.assertFalse(tj.is_alive(), "job thread did not finish -- possible deadlock")
        self.assertFalse(ta.is_alive(), "analyze thread did not finish -- possible deadlock")
        return translate_started_at["t"], translate_ended_at["t"], analyze_entered_at["t"]

    def test_unwrapped_per_stage_locking_lets_analyze_land_in_the_gap(self):
        """Reproduces the bug: with only per-stage locks, the Analyze
        thread can acquire the lock before the job's later stages even
        start -- it slips in through the gap between stream_select and
        asr, well before translate."""
        translate_started, _translate_ended, analyze_at = self._run_job_and_analyze(wrap_whole_job=False)
        self.assertLess(analyze_at, translate_started,
                         "expected the analyze click to land in a gap mid-job, before translate "
                         "even started -- this is the vulnerability the outer lock closes")

    def test_outer_lock_closes_the_gap_analyze_must_wait_for_the_whole_job(self):
        """With the fix (one gpu_lock() around the whole job), the Analyze
        thread cannot enter until every stage -- including translate --
        has finished."""
        _translate_started, translate_ended, analyze_at = self._run_job_and_analyze(wrap_whole_job=True)
        self.assertGreaterEqual(analyze_at, translate_ended,
                                 "analyze click entered before the job's last stage finished -- "
                                 "the outer lock did not close the gap")


if __name__ == "__main__":
    unittest.main()
