import pytest

from app.hardware import AcceleratorVendor, GpuDevice, HardwareProfile, detect_hardware, recommended_whisper_model


@pytest.mark.unit
def test_detect_hardware_never_raises_and_returns_a_tier():
    profile = detect_hardware()
    assert profile.vendor in (AcceleratorVendor.NVIDIA, AcceleratorVendor.AMD_ROCM, AcceleratorVendor.CPU)
    assert profile.cpu_cores >= 1
    assert profile.total_ram_mb > 0


@pytest.mark.unit
def test_cpu_only_profile_sets_fallback_reason():
    profile = HardwareProfile(vendor=AcceleratorVendor.CPU, gpu_count=0, cpu_cores=4, total_ram_mb=8192,
                               fallback_reason="No accelerator detected.")
    assert profile.fallback_reason is not None
    assert profile.total_vram_mb == 0


@pytest.mark.unit
def test_recommended_model_downgrades_on_low_vram():
    small_gpu = HardwareProfile(vendor=AcceleratorVendor.NVIDIA, gpu_count=1,
                                 gpus=[GpuDevice(index=0, name="Fake 2GB", total_vram_mb=1800)],
                                 cpu_cores=4, total_ram_mb=8192)
    assert recommended_whisper_model(small_gpu, "large-v3") in ("tiny", "base", "small")


@pytest.mark.unit
def test_recommended_model_keeps_large_on_big_gpu():
    big_gpu = HardwareProfile(vendor=AcceleratorVendor.NVIDIA, gpu_count=1,
                               gpus=[GpuDevice(index=0, name="Fake 24GB", total_vram_mb=24000)],
                               cpu_cores=16, total_ram_mb=65536)
    assert recommended_whisper_model(big_gpu, "large-v3") == "large-v3"


@pytest.mark.unit
def test_cpu_only_caps_model_size():
    cpu_profile = HardwareProfile(vendor=AcceleratorVendor.CPU, gpu_count=0, cpu_cores=8, total_ram_mb=16384)
    assert recommended_whisper_model(cpu_profile, "large-v3") == "small"
    assert recommended_whisper_model(cpu_profile, "tiny") == "tiny"
