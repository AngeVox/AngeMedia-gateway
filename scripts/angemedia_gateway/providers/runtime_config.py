"""Resolve effective built-in provider credentials and endpoints."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .. import config as C
from ..repositories.provider_runtime_config import get_provider_runtime_config


@dataclass(frozen=True)
class ResolvedProviderRuntimeConfig:
    provider_id: str
    api_key: str
    base_url: str
    base_url_override: str | None
    default_model_override: str | None
    updated_at: str | None


_API_KEY_GETTERS: dict[str, Callable[[], str]] = {
    "siliconflow": lambda: C.SILICONFLOW_API_KEY,
    "modelscope": lambda: C.MODELSCOPE_API_KEY,
    "pollinations": lambda: C.POLLINATIONS_API_KEY,
    "openai_image": lambda: C.OPENAI_IMAGE_API_KEY,
    "agnes_image": lambda: C.AGNES_API_KEY,
    "agnes_video": lambda: C.AGNES_API_KEY,
    "bytedance": lambda: C.BYTEDANCE_API_KEY,
}

_BASE_URL_GETTERS: dict[str, Callable[[], str]] = {
    "siliconflow": lambda: "https://api.siliconflow.cn/v1",
    "modelscope": lambda: "https://api-inference.modelscope.cn",
    "pollinations": lambda: "https://gen.pollinations.ai/v1",
    "openai_image": lambda: C.OPENAI_IMAGE_BASE_URL,
    "agnes_image": lambda: C.AGNES_BASE_URL,
    "agnes_video": lambda: C.AGNES_BASE_URL,
    "bytedance": lambda: C.BYTEDANCE_BASE_URL,
}


def resolve_provider_runtime_config(provider_id: str) -> ResolvedProviderRuntimeConfig:
    row = get_provider_runtime_config(provider_id) or {}
    runtime_key = str(row.get("api_key") or "").strip()
    shared_row = get_provider_runtime_config("agnes_image") if provider_id == "agnes_video" and not runtime_key else {}
    shared_key = str((shared_row or {}).get("api_key") or "").strip()
    env_key_getter = _API_KEY_GETTERS.get(provider_id, lambda: "")
    base_url_getter = _BASE_URL_GETTERS.get(provider_id, lambda: "")
    override = str(row.get("base_url_override") or "").strip().rstrip("/") or None
    shared_override = str((shared_row or {}).get("base_url_override") or "").strip().rstrip("/") or None
    base_url = override or str(base_url_getter() or "").strip().rstrip("/")
    return ResolvedProviderRuntimeConfig(
        provider_id=provider_id,
        api_key=runtime_key or shared_key or str(env_key_getter() or "").strip(),
        base_url=override or shared_override or base_url,
        base_url_override=override or shared_override,
        default_model_override=str(row.get("default_model_override") or "").strip() or None,
        updated_at=str(row.get("updated_at") or (shared_row or {}).get("updated_at") or "").strip() or None,
    )


def provider_key_preview(value: str) -> str | None:
    secret = str(value or "")
    if not secret:
        return None
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:3]}...{secret[-4:]}"
