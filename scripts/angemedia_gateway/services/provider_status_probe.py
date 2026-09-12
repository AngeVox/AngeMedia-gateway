"""Custom provider status/quota probe helpers."""
from __future__ import annotations

import asyncio
from typing import Any

from ..providers.http import provider_client
from ..providers.endpoint_policy import validate_provider_probe_url


PROVIDER_STATUS_TIMEOUT_SECONDS = 3.0
PROVIDER_STATUS_CONCURRENCY = 4


async def enrich_custom_provider_status(
    provider: dict[str, Any],
    *,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    item = dict(provider)
    async with semaphore:
        for key in ("status_url", "quota_url"):
            url = provider.get(key)
            if not url:
                continue
            item[key.replace("_url", "")] = await probe_provider_url(
                str(url), provider_id=str(provider.get("id") or "").strip() or None
            )
    item.pop("_api_key", None)
    return item


async def probe_provider_url(url: str, *, provider_id: str | None = None) -> dict[str, Any]:
    try:
        safe_url = validate_provider_probe_url(url)
        async with provider_client(timeout=PROVIDER_STATUS_TIMEOUT_SECONDS, provider_id=provider_id) as client:
            resp = await client.get(safe_url, headers={})
        return {
            "ok": resp.status_code < 400,
            "http_status": resp.status_code,
            "error": None,
        }
    except Exception:
        return {"ok": False, "http_status": None, "error": "连接失败"}
