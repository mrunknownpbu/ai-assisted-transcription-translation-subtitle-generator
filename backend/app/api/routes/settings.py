"""Read-only, non-secret configuration surface for the Settings page. Deliberately narrow:
only values safe to show in a UI (model choices, feature availability) -- never API keys or
filesystem paths that could leak host layout."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.schemas import SettingsOut
from app.config import Settings, get_settings
from app.pipeline.stage8_translation.tvdb_client import configured as tvdb_configured

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsOut)
def get_app_settings(settings: Settings = Depends(get_settings)):
    return SettingsOut(
        whisper_model_size=settings.whisper_model_size,
        nllb_model_name=settings.nllb_model_name,
        max_concurrent_gpu_jobs=settings.max_concurrent_gpu_jobs,
        library_configured=settings.library_dir is not None,
        tvdb_configured=tvdb_configured(),
    )
