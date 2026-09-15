import pytest

from app.core.gpu import unload_engines


class _EngineWithUnload:
    def __init__(self, name):
        self.name = name
        self.unloaded = False

    def unload(self):
        self.unloaded = True


class _EngineWithoutUnload:
    def __init__(self, name):
        self.name = name


class _EngineWhoseUnloadRaises:
    name = "broken"

    def unload(self):
        raise RuntimeError("simulated failure freeing this engine")


@pytest.mark.unit
def test_unload_engines_calls_unload_on_every_engine_that_has_it():
    e1, e2 = _EngineWithUnload("a"), _EngineWithUnload("b")

    unload_engines([e1, e2])

    assert e1.unloaded is True
    assert e2.unloaded is True


@pytest.mark.unit
def test_unload_engines_skips_engines_without_an_unload_method():
    engine = _EngineWithoutUnload("cpu-only")

    unload_engines([engine])  # must not raise just because .unload() doesn't exist


@pytest.mark.unit
def test_unload_engines_is_resilient_to_a_single_engine_failing():
    ok_engine = _EngineWithUnload("ok")
    broken_engine = _EngineWhoseUnloadRaises()

    unload_engines([broken_engine, ok_engine])  # must not raise; broken engine shouldn't block the rest

    assert ok_engine.unloaded is True


@pytest.mark.unit
def test_unload_engines_handles_an_empty_list():
    unload_engines([])


@pytest.mark.unit
def test_with_gpu_only_releases_the_slot_after_the_wrapped_work_finishes(monkeypatch):
    """Regression test for the ordering the whole fix depends on: whatever GPU cleanup a
    caller does inside its `_with_gpu`-wrapped function must complete before the slot is
    released, or a waiting job can start loading its own model on top of memory this job
    hasn't freed yet."""
    from app.config import Settings
    from app.hardware import AcceleratorVendor, HardwareProfile
    from app.jobs.worker import JobRunner

    call_order = []
    monkeypatch.setattr("app.jobs.worker.try_acquire_gpu_slot", lambda session, job_id: call_order.append("acquire") or True)
    monkeypatch.setattr("app.jobs.worker.release_gpu_slot", lambda session, job_id: call_order.append("release"))

    profile = HardwareProfile(vendor=AcceleratorVendor.NVIDIA, gpu_count=1, cpu_cores=4, total_ram_mb=8192)
    runner = JobRunner(settings=Settings(), hardware_profile=profile)

    def fn():
        call_order.append("unload")  # stands in for unload_engines() running inside a real caller's closure
        return "result"

    result = runner._with_gpu(session=None, job_id="job1", fn=fn)

    assert result == "result"
    assert call_order == ["acquire", "unload", "release"]
