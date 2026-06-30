"""Assistant LLM configuration, model discovery, and connection testing."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..assistant import assistant_allow_agnes, assistant_allow_paid, assistant_enabled
from ..repositories.settings import get_config
from ..security import redact_secret_text


class AssistantConfigError(Exception):
    """Raised when assistant admin endpoints are missing required config."""


class AssistantModelFetchError(Exception):
    """Raised when assistant /models lookup fails."""


class AssistantConnectionTestError(Exception):
    """Raised when assistant chat completion test fails."""


@dataclass(frozen=True)
class AssistantRuntimeConfig:
    base_url: str
    api_key: str
    model: str
    key_source: str


def assistant_config_summary() -> dict[str, Any]:
    api_key = get_config("ANGE_LLM_API_KEY", os.getenv("ANGE_LLM_API_KEY", "")).strip()
    return {
        "enabled": assistant_enabled(),
        "allow_paid": assistant_allow_paid(),
        "allow_agnes": assistant_allow_agnes(),
        "llm_model": get_config("ANGE_LLM_MODEL", os.getenv("ANGE_LLM_MODEL", "")),
        "llm_base_url": get_config("ANGE_LLM_BASE_URL", os.getenv("ANGE_LLM_BASE_URL", "")),
        "configured": bool(api_key),
    }


def resolve_assistant_runtime(payload: dict[str, Any] | None = None) -> AssistantRuntimeConfig:
    payload = payload or {}
    use_empty_key = payload.get("use_empty_api_key") is True
    raw_api_key = payload.get("api_key") if "api_key" in payload else None
    form_key = str(raw_api_key or "").strip()
    if form_key:
        api_key = form_key
        key_source = "form"
    elif use_empty_key:
        api_key = ""
        key_source = "empty"
    else:
        api_key = get_config("ANGE_LLM_API_KEY", os.getenv("ANGE_LLM_API_KEY", "")).strip()
        key_source = "saved" if api_key else "none"

    base_url_value = payload["base_url"] if "base_url" in payload else get_config("ANGE_LLM_BASE_URL", os.getenv("ANGE_LLM_BASE_URL", ""))
    model_value = payload["model"] if "model" in payload else get_config("ANGE_LLM_MODEL", os.getenv("ANGE_LLM_MODEL", ""))
    return AssistantRuntimeConfig(
        base_url=str(base_url_value or "").strip().rstrip("/"),
        api_key=api_key,
        model=str(model_value or "").strip(),
        key_source=key_source,
    )


async def fetch_assistant_model_ids(base_url: str, api_key: str, timeout: float = 15.0) -> tuple[list[str], int]:
    started = time.perf_counter()
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if resp.status_code >= 400:
        raise AssistantModelFetchError(f"模型列表拉取失败：HTTP {resp.status_code}")
    data = resp.json()
    ids = []
    for item in data.get("data", []):
        model_id = item.get("id") if isinstance(item, dict) else None
        if model_id:
            ids.append(str(model_id))
    return assistant_chat_model_ids(ids), elapsed_ms


def assistant_chat_model_ids(model_ids: list[str]) -> list[str]:
    """Keep likely text/chat models out of mixed media model registries."""
    blocked_tokens = (
        "image", "img", "video", "audio", "speech", "whisper", "tts", "voice",
        "embedding", "embed", "rerank", "bge", "e5-", "gte-", "jina-embedding",
        "flux", "kolors", "agnes", "z-image",
        "stable-diffusion", "sdxl", "dall-e", "midjourney",
    )
    result: list[str] = []
    for model_id in model_ids:
        value = str(model_id).strip()
        if not value:
            continue
        lower = value.lower()
        if any(token in lower for token in blocked_tokens):
            continue
        result.append(value)
    return sorted(set(result))


class AssistantConfigService:
    async def list_models(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        config = resolve_assistant_runtime(payload)
        if not config.base_url:
            raise AssistantConfigError("请先配置 LLM 接口地址")
        try:
            models, elapsed_ms = await fetch_assistant_model_ids(config.base_url, config.api_key)
        except AssistantModelFetchError:
            raise
        except Exception as exc:
            raise AssistantModelFetchError("模型列表拉取失败") from exc
        return {
            "data": models,
            "elapsed_ms": elapsed_ms,
            "base_url": config.base_url,
            "key_source": config.key_source,
        }

    async def test_connection(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        config = resolve_assistant_runtime(payload)
        if not config.base_url or not config.model:
            raise AssistantConfigError("请先配置 LLM 接口地址和模型")
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{config.base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": config.model,
                        "temperature": 0.1,
                        "max_tokens": 48,
                        "messages": [
                            {"role": "system", "content": "你是 AngeMedia 连通性测试助手。"},
                            {"role": "user", "content": "请用中文用一句话回复：AngeMedia 小助手连接正常。"},
                        ],
                    },
                )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if resp.status_code >= 400:
                raise AssistantConnectionTestError(f"LLM 测试失败：HTTP {resp.status_code}")
            data = resp.json()
            content = str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
            return {
                "ok": True,
                "model": config.model,
                "elapsed_ms": elapsed_ms,
                "preview": redact_secret_text(content)[:200],
                "key_source": config.key_source,
            }
        except AssistantConnectionTestError:
            raise
        except Exception as exc:
            raise AssistantConnectionTestError("LLM 测试失败") from exc
