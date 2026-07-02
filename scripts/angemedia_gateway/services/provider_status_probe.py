"""Custom provider status/quota probe helpers."""
from __future__ import annotations

import asyncio
from typing import Any

from ..outbound_http import outbound_client
from ..security import ensure_public_http_url


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
            item[key.replace("_url", "")] = await probe_provider_url(str(url))
    item.pop("_api_key", None)
    return item


async def probe_provider_url(url: str) -> dict[str, Any]:
    try:
        safe_url = ensure_public_http_url(url)
        async with outbound_client(timeout=PROVIDER_STATUS_TIMEOUT_SECONDS) as client:
            resp = await client.get(safe_url, headers={})
        return {
            "ok": resp.status_code < 400,
            "http_status": resp.status_code,
            "error": None,
        }
    except Exception:
        return {"ok": False, "http_status": None, "error": "连接失败"}
