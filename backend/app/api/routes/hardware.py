from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends

from app.api.schemas import HardwareProfileOut, StorageOut
from app.config import Settings, get_settings
from app.hardware import detect_hardware

router = APIRouter(prefix="/api/hardware", tags=["hardware"])


@router.get("", response_model=HardwareProfileOut)
def get_hardware():
    profile = detect_hardware()
    return HardwareProfileOut(
        vendor=profile.vendor.value, gpu_count=profile.gpu_count,
        gpus=[g.__dict__ for g in profile.gpus], cpu_cores=profile.cpu_cores,
        total_ram_mb=profile.total_ram_mb, fallback_reason=profile.fallback_reason,
    )


@router.get("/storage", response_model=StorageOut)
def get_storage(settings: Settings = Depends(get_settings)):
    """Real disk usage of the filesystem backing the output volume -- never fabricated
    placeholder numbers. Falls back to the media volume if output isn't mounted yet."""
    target = settings.output_dir if settings.output_dir.exists() else settings.media_dir
    usage = shutil.disk_usage(target)
    return StorageOut(total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free)
