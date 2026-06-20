"""Thin facade for media generation orchestration."""
from __future__ import annotations

import logging

from ..media import localize_image_result, localize_video_result, maybe_to_b64
from ..providers.custom import generate_custom_openai_image
from ..repositories.generations import record_generation
from ..repositories.jobs import create_job, get_job_by_external_task_id, update_job_status
from ..repositories.settings import builtin_provider_enabled, get_custom_provider
from ..repositories.video_tasks import upsert_video_task
from ..routing import resolve_chain
from ..runtime import PROVIDERS, agnes_video
from ..schemas import ImageRequest, VideoRequest
from .generation_assets import save_generated_asset as _save_generated_asset
from .image_generation import (
    CustomProviderNotFound,
    ImageProvidersFailed,
    NoImageProviderAvailable,
    create_image,
)
from .job_lifecycle import JobLifecycle
from .video_generation import InvalidVideoReference, VideoProviderDisabled
from .video_generation import create_video as create_video_orchestration
from .video_generation import get_video as get_video_orchestration

log = logging.getLogger("angemedia-gateway")


class MediaService:
    async def create_image(self, req: ImageRequest) -> dict:
        return await create_image(
            req,
            providers=PROVIDERS,
            resolve_chain_func=resolve_chain,
            get_custom_provider_func=get_custom_provider,
            generate_custom_image_func=generate_custom_openai_image,
            localize_image_result_func=localize_image_result,
            maybe_to_b64_func=maybe_to_b64,
            record_generation_func=record_generation,
            save_generated_asset_func=_save_generated_asset,
            job_lifecycle=_job_lifecycle(),
        )

    async def create_video(self, req: VideoRequest) -> dict:
        return await create_video_orchestration(
            req,
            agnes_video_provider=agnes_video,
            builtin_provider_enabled_func=builtin_provider_enabled,
            localize_video_result_func=localize_video_result,
            record_generation_func=record_generation,
            upsert_video_task_func=upsert_video_task,
            save_generated_asset_func=_save_generated_asset,
            job_lifecycle=_job_lifecycle(),
        )

    async def get_video(self, task_id: str) -> dict:
        return await get_video_orchestration(
            task_id,
            agnes_video_provider=agnes_video,
            localize_video_result_func=localize_video_result,
            upsert_video_task_func=upsert_video_task,
            get_job_by_external_task_id_func=get_job_by_external_task_id,
            save_generated_asset_func=_save_generated_asset,
            job_lifecycle=_job_lifecycle(),
        )


def _job_lifecycle() -> JobLifecycle:
    return JobLifecycle(
        create_job_func=create_job,
        update_job_status_func=update_job_status,
        logger=log,
    )
