"""Hardware auto-detection: NVIDIA CUDA, AMD ROCm, or CPU-only.

Runs once at process startup (both the API process and each worker process call this
independently, since a worker may land on a different host in a future multi-node setup).
Never raises — an accelerator detection failure always degrades to the next tier down to
CPU-only rather than crashing the process.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum

import psutil

logger = logging.getLogger("subtitle_platform.hardware")


class AcceleratorVendor(str, Enum):
    NVIDIA = "nvidia"
    AMD_ROCM = "rocm"
    CPU = "cpu"


@dataclass
class GpuDevice:
    index: int
    name: str
    total_vram_mb: int


@dataclass
class HardwareProfile:
    vendor: AcceleratorVendor
    gpu_count: int
    gpus: list[GpuDevice] = field(default_factory=list)
    cpu_cores: int = 0
    total_ram_mb: int = 0
    fallback_reason: str | None = None  # populated when we degraded from a preferred tier

    @property
    def total_vram_mb(self) -> int:
        return sum(g.total_vram_mb for g in self.gpus)

    def as_dict(self) -> dict:
        return {
            "vendor": self.vendor.value,
            "gpu_count": self.gpu_count,
            "gpus": [g.__dict__ for g in self.gpus],
            "cpu_cores": self.cpu_cores,
            "total_ram_mb": self.total_ram_mb,
            "fallback_reason": self.fallback_reason,
        }


def _cpu_info() -> tuple[int, int]:
    cores = psutil.cpu_count(logical=True) or 1
    total_ram_mb = int(psutil.virtual_memory().total / (1024 * 1024))
    return cores, total_ram_mb


def _detect_nvidia() -> list[GpuDevice] | None:
    """Prefer torch's CUDA view (matches what the ASR/translation engines will actually
    see); fall back to parsing nvidia-smi if torch isn't importable yet or reports nothing
    while the tool is clearly present, so a broken torch install doesn't masquerade as
    'no GPU'."""
    devices: list[GpuDevice] = []
    try:
        import torch

        if torch.cuda.is_available() and getattr(torch.version, "hip", None) is None:
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                devices.append(GpuDevice(index=i, name=props.name, total_vram_mb=int(props.total_memory / (1024 * 1024))))
            if devices:
                return devices
    except Exception as exc:  # torch not installed, driver mismatch, etc.
        logger.debug("torch CUDA probe failed: %s", exc)

    if shutil.which("nvidia-smi") is None:
        return None

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        for line in out.stdout.strip().splitlines():
            idx_s, name, mem_s = [p.strip() for p in line.split(",")]
            devices.append(GpuDevice(index=int(idx_s), name=name, total_vram_mb=int(float(mem_s))))
    except Exception as exc:
        logger.warning("nvidia-smi present but query failed: %s", exc)
        return None
    return devices or None


def _detect_rocm() -> list[GpuDevice] | None:
    devices: list[GpuDevice] = []
    try:
        import torch

        if torch.cuda.is_available() and getattr(torch.version, "hip", None) is not None:
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                devices.append(GpuDevice(index=i, name=props.name, total_vram_mb=int(props.total_memory / (1024 * 1024))))
            if devices:
                return devices
    except Exception as exc:
        logger.debug("torch ROCm probe failed: %s", exc)

    if shutil.which("rocminfo") is None:
        return None

    try:
        out = subprocess.run(["rocminfo"], capture_output=True, text=True, timeout=10, check=True)
        # rocminfo's text format has no stable per-device VRAM line we can parse reliably
        # without the ROCm SMI tool; count "Agent" GPU blocks and report VRAM as unknown (0)
        # rather than guessing. Documented as best-effort in docs/DEPLOYMENT.md.
        gpu_blocks = [l for l in out.stdout.splitlines() if "gfx" in l.lower()]
        for i, _ in enumerate(gpu_blocks):
            devices.append(GpuDevice(index=i, name="AMD ROCm GPU", total_vram_mb=0))
    except Exception as exc:
        logger.warning("rocminfo present but query failed: %s", exc)
        return None
    return devices or None


def detect_hardware() -> HardwareProfile:
    cores, ram_mb = _cpu_info()

    nvidia = _detect_nvidia()
    if nvidia:
        profile = HardwareProfile(vendor=AcceleratorVendor.NVIDIA, gpu_count=len(nvidia), gpus=nvidia,
                                   cpu_cores=cores, total_ram_mb=ram_mb)
        logger.info("Hardware: NVIDIA CUDA, %d GPU(s): %s", len(nvidia), [g.name for g in nvidia])
        return profile

    rocm = _detect_rocm()
    if rocm:
        profile = HardwareProfile(vendor=AcceleratorVendor.AMD_ROCM, gpu_count=len(rocm), gpus=rocm,
                                   cpu_cores=cores, total_ram_mb=ram_mb)
        logger.info("Hardware: AMD ROCm, %d GPU(s)", len(rocm))
        return profile

    profile = HardwareProfile(
        vendor=AcceleratorVendor.CPU, gpu_count=0, gpus=[], cpu_cores=cores, total_ram_mb=ram_mb,
        fallback_reason="No NVIDIA CUDA or AMD ROCm device detected; running CPU-only.",
    )
    logger.warning("Hardware: no accelerator detected, falling back to CPU-only (%d cores, %d MB RAM)", cores, ram_mb)
    return profile


def recommended_whisper_model(profile: HardwareProfile, requested: str) -> str:
    """Auto-downgrade the requested Whisper model size when VRAM is too small, so a job
    doesn't OOM instead of just running slower on a smaller model."""
    if profile.vendor == AcceleratorVendor.CPU:
        # large models are impractically slow on CPU; cap at 'small' unless explicitly tiny/base
        return requested if requested in ("tiny", "base", "small") else "small"

    vram = profile.total_vram_mb
    tiers = [("large-v3", 10_000), ("medium", 5_000), ("small", 2_000), ("base", 1_000), ("tiny", 0)]
    for name, min_vram in tiers:
        if requested == name and vram < min_vram:
            for fallback_name, fallback_min in tiers:
                if vram >= fallback_min:
                    logger.warning("Requested Whisper model '%s' needs ~%dMB VRAM, only %dMB available; using '%s'",
                                   requested, min_vram, vram, fallback_name)
                    return fallback_name
    return requested
