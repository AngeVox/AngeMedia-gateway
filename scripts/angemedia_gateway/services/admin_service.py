"""Admin read-model assembly helpers."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from .. import config as C
from ..runtime import refresh_runtime
from ..security import generate_gateway_key
from .assistant_config_service import assistant_config_summary
from .provider_status_probe import PROVIDER_STATUS_CONCURRENCY, enrich_custom_provider_status
from ..repositories.settings import (
    BUILTIN_PROVIDER_CONFIG_KEYS,
    builtin_provider_enabled,
    config_snapshot,
    delete_custom_provider as delete_custom_provider_state,
    get_config,
    list_custom_providers,
    set_builtin_provider_enabled,
    set_config_many,
    update_custom_provider_enabled,
    update_custom_provider_sort,
)


BUILTIN_PROVIDER_META: list[dict[str, Any]] = [
    {
        "id": "siliconflow",
        "name": "SiliconFlow",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["kolors"],
        "default_model": "Kwai-Kolors/Kolors",
        "sort_order": 10,
        "description": "默认链路首选，适合通用文生图。",
    },
    {
        "id": "modelscope",
        "name": "ModelScope",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["qwen", "flux", "z-image", "z-turbo"],
        "default_model": "Qwen/Qwen-Image-2512",
        "sort_order": 20,
        "description": "承载 Qwen、FLUX、Z-Image 等默认图片模型。",
    },
    {
        "id": "pollinations",
        "name": "Pollinations",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["pollinations"],
        "default_model": C.POLLINATIONS_DEFAULT_MODEL,
        "sort_order": 90,
        "description": "实验性图片渠道，默认关闭，不在默认链中。",
    },
    {
        "id": "agnes_image",
        "name": "Agnes Image",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["agnes-image", "agnes-2.5", "agnes-2.1", "agnes-2.0"],
        "default_model": C.AGNES_IMAGE_MODEL,
        "sort_order": 40,
        "description": "Agnes 图片模型，需要 Agnes 密钥。",
    },
    {
        "id": "openai_image",
        "name": "OpenAI-compatible Image",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["openai-image", "gpt-image-2.5-sunburst", "openai-flare", "gpt-image-2"],
        "default_model": C.OPENAI_IMAGE_MODEL,
        "sort_order": 50,
        "description": "显式 OpenAI-compatible 图片渠道，不进入免费默认链路。",
    },
    {
        "id": "agnes_video",
        "name": "Agnes Video",
        "provider_type": "built_in_video",
        "category": "视频",
        "aliases": ["agnes-video-2.5", "agnes-video-v2.0"],
        "default_model": "agnes-video-v2.0",
        "sort_order": 60,
        "description": "视频任务提交和状态查询渠道。",
    },
    {
        "id": "mock",
        "name": "Mock (测试)",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["mock"],
        "default_model": "mock-model",
        "sort_order": 99,
        "description": "测试用 Mock Provider，返回固定测试图片，不发起任何外部请求。",
    },
]

PROVIDER_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "openai-images",
        "name": "OpenAI Images 兼容",
        "description": "标准 /v1/images/generations 接口，适合 OpenAI、转发站或兼容代理。",
        "provider_type": "openai_image",
        "payload": {
            "name": "OpenAI Images",
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-image-2.5-sunburst",
            "sort_order": 100,
        },
    },
    {
        "id": "new-api-images",
        "name": "New-API 图片渠道",
        "description": "New-API 中已接入图片模型时可用；按你的部署替换根地址和模型名。",
        "provider_type": "openai_image",
        "payload": {
            "name": "New-API Images",
            "base_url": "https://your-new-api.example.com/v1",
            "default_model": "gpt-image-2.5-sunburst",
            "sort_order": 110,
        },
    },
    {
        "id": "custom-images",
        "name": "自定义图片服务",
        "description": "任何返回 data[0].url 或 data[0].b64_json 的 OpenAI Images 兼容服务。",
        "provider_type": "openai_image",
        "payload": {
            "name": "Custom Images",
            "base_url": "https://example.com/v1",
            "default_model": "your-image-model",
            "sort_order": 120,
        },
    },
]


class AdminService:
    def builtin_configured(self, provider_id: str) -> bool:
        if provider_id == "siliconflow":
            return bool(get_config("SILICONFLOW_API_KEY", C.SILICONFLOW_API_KEY))
        if provider_id == "modelscope":
            return bool(get_config("MODELSCOPE_API_KEY", C.MODELSCOPE_API_KEY))
        if provider_id == "pollinations":
            return True
        if provider_id == "openai_image":
            fallback = os.getenv("OPENAI_API_KEY", C.OPENAI_IMAGE_API_KEY)
            return bool(get_config("OPENAI_IMAGE_API_KEY", fallback))
        if provider_id in {"agnes_image", "agnes_video"}:
            return bool(get_config("AGNES_API_KEY", C.AGNES_API_KEY))
        if provider_id == "mock":
            return True
        return False

    def builtin_provider_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for meta in BUILTIN_PROVIDER_META:
            enabled = builtin_provider_enabled(str(meta["id"]))
            configured = self.builtin_configured(str(meta["id"]))
            default_model = str(meta["default_model"])
            if meta["id"] == "openai_image":
                default_model = C.OPENAI_IMAGE_MODEL
            elif meta["id"] == "agnes_image":
                default_model = C.AGNES_IMAGE_MODEL
            elif meta["id"] == "pollinations":
                default_model = C.POLLINATIONS_DEFAULT_MODEL
            rows.append({
                **meta,
                "type": "built_in",
                "source": "built_in",
                "default_model": default_model,
                "enabled": enabled,
                "configured": configured,
                "ready": bool(enabled and configured),
                "removable": False,
                "last_test_status": "configured" if configured else "missing_config",
                "last_response_ms": 0,
            })
        return rows

    def custom_provider_status_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for provider in list_custom_providers(include_secret=False):
            provider.pop("api_key", None)
            enabled = bool(provider.get("enabled"))
            configured = bool(provider.get("base_url") and provider.get("default_model"))
            row = {
                **provider,
                "type": provider.get("provider_type", "openai_image"),
                "source": "custom",
                "category": "图片",
                "aliases": [f"custom:{provider['id']}"],
                "ready": bool(enabled and configured),
                "configured": configured,
                "removable": True,
            }
            rows.append(row)
        return rows

    def provider_studio_summary(self, provider: dict[str, Any] | None) -> dict[str, Any] | None:
        if provider is None:
            return None
        is_builtin = provider.get("source") == "built_in" or provider.get("type") == "built_in"
        api_key_configured = bool(provider.get("configured")) if is_builtin else bool(
            provider.get("api_key_configured") or provider.get("api_key")
        )
        return {
            "id": provider.get("id"),
            "name": provider.get("name"),
            "provider_type": provider.get("provider_type"),
            "enabled": bool(provider.get("enabled")),
            "api_key_configured": api_key_configured,
            "default_model": provider.get("default_model"),
            "capabilities": provider.get("capabilities") or {},
            "sort_order": provider.get("sort_order"),
            "last_test_status": provider.get("last_test_status"),
            "last_response_ms": provider.get("last_response_ms"),
            "last_test_at": provider.get("last_test_at"),
            "created_at": provider.get("created_at"),
            "updated_at": provider.get("updated_at"),
        }

    def provider_studio_summaries(self, providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [summary for provider in providers if (summary := self.provider_studio_summary(provider)) is not None]

    def provider_templates(self) -> list[dict[str, Any]]:
        return PROVIDER_TEMPLATES

    def custom_providers(self) -> list[dict[str, Any]]:
        return self.provider_studio_summaries(list_custom_providers(include_secret=False))

    def admin_config(self) -> dict[str, Any]:
        return {
            "settings": config_snapshot(mask=True),
            "db_file": str(C.DB_FILE),
            "upload_dir": str(C.UPLOAD_DIR),
            "output_dir": str(C.OUTPUT_DIR),
            "assistant": assistant_config_summary(),
            "custom_providers": list_custom_providers(include_secret=False),
            "provider_templates": PROVIDER_TEMPLATES,
        }

    def save_config(self, settings: dict[str, Any]) -> dict[str, Any]:
        set_config_many(settings)
        refresh_runtime()
        return self.admin_config()

    def create_gateway_key(self, save: bool) -> dict[str, Any]:
        key = generate_gateway_key()
        if save:
            set_config_many({"GATEWAY_API_KEY": key})
            refresh_runtime()
            return {"saved": True, "key_preview": key[:7] + "****" + key[-4:]}
        return {"key": key, "saved": False}

    def set_provider_enabled(self, provider_id: str, enabled: bool) -> dict[str, Any] | None:
        if provider_id in BUILTIN_PROVIDER_CONFIG_KEYS:
            set_builtin_provider_enabled(provider_id, enabled)
            refresh_runtime()
            return self.provider_studio_summary(next((row for row in self.builtin_provider_rows() if row["id"] == provider_id), None))
        return self.provider_studio_summary(update_custom_provider_enabled(provider_id, enabled))

    def sort_provider(self, provider_id: str, sort_order: int) -> dict[str, Any]:
        return self.provider_studio_summary(update_custom_provider_sort(provider_id, sort_order)) or {}

    def delete_provider(self, provider_id: str) -> bool:
        return delete_custom_provider_state(provider_id)

    async def provider_status(self) -> dict[str, Any]:
        built_in = self.builtin_provider_rows()
        semaphore = asyncio.Semaphore(PROVIDER_STATUS_CONCURRENCY)
        custom_status = await asyncio.gather(*(
            enrich_custom_provider_status(provider, semaphore=semaphore)
            for provider in self.custom_provider_status_rows()
        ))
        return {"built_in": built_in, "custom": custom_status, "data": [*built_in, *custom_status]}
