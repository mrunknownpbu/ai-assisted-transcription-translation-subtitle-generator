"""VRAM pre-flight headroom check (IMPROVEMENT_PLAN.md 2.2): a Tdarr/
Jellyfin transcode burst on a shared card can eat headroom between this
project's own idle-eviction freeing memory and the next model load
needing it -- gpu_lock() alone only serializes THIS project's own
processes, so it can't see or wait out that kind of external contention.
preflight_vram_check() polls torch.cuda.mem_get_info() and waits (bounded)
instead of letting the load attempt fail with an unrecoverable CUDA OOM.

torch.cuda is mocked throughout -- these tests are about the polling/
timeout/no-op logic in gpu.py, not real CUDA (see the real deployment's
own measured OOM incidents cited in gpu.py's module docstring for that)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import gpu


class NoCudaTests(unittest.TestCase):
    def test_no_op_when_cuda_unavailable(self):
        """CPU-only CI/tests must never block or raise here -- there is no
        VRAM to check, so the function should return immediately."""
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": fake_torch}):
            gpu.preflight_vram_check(3.2, max_wait_seconds=5)
        fake_torch.cuda.mem_get_info.assert_not_called()


class SufficientVramTests(unittest.TestCase):
    def test_returns_immediately_when_enough_free_on_first_check(self):
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.mem_get_info.return_value = (4 * 1024**3, 8 * 1024**3)
        with patch.dict("sys.modules", {"torch": fake_torch}), \
             patch("time.sleep") as sleep:
            gpu.preflight_vram_check(3.2, max_wait_seconds=20)
        fake_torch.cuda.mem_get_info.assert_called_once()
        sleep.assert_not_called()


class WaitAndRetryTests(unittest.TestCase):
    def test_polls_until_headroom_frees_up_then_returns(self):
        """First check: not enough free (a transcode burst). Second check
        (after one simulated sleep): enough free -- must return without
        raising, having polled exactly twice."""
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.mem_get_info.side_effect = [
            (1 * 1024**3, 8 * 1024**3),   # 1GB free -- not enough
            (4 * 1024**3, 8 * 1024**3),   # 4GB free -- enough now
        ]
        with patch.dict("sys.modules", {"torch": fake_torch}), \
             patch("time.sleep") as sleep, \
             patch("time.monotonic", side_effect=[0.0, 0.5, 0.5, 20.0]):
            gpu.preflight_vram_check(3.2, max_wait_seconds=20, poll_interval_seconds=2)
        self.assertEqual(fake_torch.cuda.mem_get_info.call_count, 2)
        sleep.assert_called_once()


class TimeoutTests(unittest.TestCase):
    def test_raises_insufficient_vram_error_after_window_expires(self):
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.mem_get_info.return_value = (0.5 * 1024**3, 8 * 1024**3)
        with patch.dict("sys.modules", {"torch": fake_torch}), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0.0, 25.0]):
            with self.assertRaises(gpu.InsufficientVramError) as ctx:
                gpu.preflight_vram_check(3.2, max_wait_seconds=20, poll_interval_seconds=2)
        # The error message is operator-facing (shows up in a failed-job
        # log) -- must actually say what was free and what was required,
        # not just "insufficient".
        self.assertIn("0.50", str(ctx.exception))
        self.assertIn("3.2", str(ctx.exception))

    def test_never_sleeps_past_the_deadline(self):
        """A poll_interval_seconds longer than the remaining window must
        not cause the actual wait to overshoot max_wait_seconds."""
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.mem_get_info.return_value = (0.1 * 1024**3, 8 * 1024**3)
        with patch.dict("sys.modules", {"torch": fake_torch}), \
             patch("time.sleep") as sleep, \
             patch("time.monotonic", side_effect=[0.0, 3.0, 6.0, 21.0]):
            with self.assertRaises(gpu.InsufficientVramError):
                gpu.preflight_vram_check(3.2, max_wait_seconds=20, poll_interval_seconds=10)
        for call in sleep.call_args_list:
            self.assertLessEqual(call.args[0], 20)


class DefaultMarginTests(unittest.TestCase):
    def test_default_margin_is_3_2gb_unless_overridden_by_env(self):
        import importlib
        import os
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUBTITLE_AI_VRAM_MARGIN_GB", None)
            reloaded = importlib.reload(gpu)
        self.assertEqual(reloaded.DEFAULT_VRAM_MARGIN_GB, 3.2)
        importlib.reload(gpu)  # restore for any tests running after this one

    def test_env_var_overrides_default_margin(self):
        import importlib
        import os
        with patch.dict(os.environ, {"SUBTITLE_AI_VRAM_MARGIN_GB": "2.0"}):
            reloaded = importlib.reload(gpu)
            self.assertEqual(reloaded.DEFAULT_VRAM_MARGIN_GB, 2.0)
        importlib.reload(gpu)  # restore real env for any tests running after this one


if __name__ == "__main__":
    unittest.main()
