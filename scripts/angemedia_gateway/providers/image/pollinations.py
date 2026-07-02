"""Pollinations image adapter."""
from __future__ import annotations

import base64
import time
import urllib.parse
from typing import Any

from ... import config as C
from ...media import openai_image_response
from ...outbound_http import outbound_client
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable, RateLimited
from ..parsers import parse_size
from ..runtime_config import resolve_provider_runtime_config


class PollinationsProvider:
    name = "pollinations"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        width, height = parse_size(req.size)
        runtime = resolve_provider_runtime_config(self.name)

        if runtime.api_key:
            payload: dict[str, Any] = {
                "prompt": req.prompt,
                "model": target.model or C.POLLINATIONS_DEFAULT_MODEL,
                "n": 1,
                "size": f"{width}x{height}",
                "response_format": req.response_format,
            }
            if req.safe is not None:
                payload["safe"] = req.safe
            async with outbound_client(timeout=C.HTTP_TIMEOUT) as client:
                resp = await client.post(
                    f"{runtime.base_url}/images/generations",
                    headers={
                        "Authorization": f"Bearer {runtime.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            if resp.status_code == 429:
                raise RateLimited("Pollinations rate limited")
            if resp.status_code != 200:
                raise BackendUnavailable(f"Pollinations 上游返回 HTTP {resp.status_code}", status_code=resp.status_code)
            return resp.json()

        encoded = urllib.parse.quote(req.prompt)
        query = {
            "width": str(width),
            "height": str(height),
            "model": target.model or C.POLLINATIONS_DEFAULT_MODEL,
            "nologo": "true",
        }
        if req.safe is not None:
            query["safe"] = str(req.safe).lower()
        legacy_url = f"https://image.pollinations.ai/prompt/{encoded}?{urllib.parse.urlencode(query)}"

        async with outbound_client(follow_redirects=True, timeout=C.HTTP_TIMEOUT) as client:
            resp = await client.get(legacy_url)
        content_type = resp.headers.get("content-type", "")
        if resp.status_code != 200 or not content_type.startswith("image/"):
            raise BackendUnavailable(f"Pollinations legacy endpoint failed: {resp.status_code} {content_type}")

        ext = "png" if "png" in content_type else "jpg"
        filename = f"pollinations_{int(time.time() * 1000)}.{ext}"
        path = C.OUTPUT_DIR / filename
        path.write_bytes(resp.content)
        if req.response_format == "b64_json":
            return openai_image_response(b64_json=base64.b64encode(resp.content).decode("ascii"))
        return openai_image_response(url=f"{C.PUBLIC_BASE_URL}/generated/{filename}")

    def health(self) -> str:
        return "configured_key" if resolve_provider_runtime_config(self.name).api_key else "legacy_public_endpoint"
