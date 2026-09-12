"""ModelScope hosted image inference adapter."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlparse

from ... import config as C
from ...media import openai_image_response
from ...reference_images import (
    collect_image_reference_values,
    is_safe_image_data_url,
    materialize_gateway_image_reference,
)
from ...schemas import ImageRequest
from ...security import validate_provider_reference_url
from ..base import RouteTarget
from ..errors import BackendUnavailable, RateLimited
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import resolve_provider_runtime_config
from .quota import quota

log = logging.getLogger("angemedia-gateway")


class ModelScopeProvider:
    name = "modelscope"

    @staticmethod
    def _reference_payload(req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        references = collect_image_reference_values(req)
        if not references:
            return {}

        data_urls: list[str] = []
        remote_urls: list[str] = []
        for reference in references:
            text = str(reference or "").strip()
            if text.startswith(("/uploads/", "/generated/")):
                data_urls.append(materialize_gateway_image_reference(text))
                continue
            if is_safe_image_data_url(text):
                data_urls.append(text)
                continue
            parsed = urlparse(text)
            if parsed.scheme in {"http", "https"}:
                try:
                    remote_urls.append(validate_provider_reference_url(text))
                except ValueError as exc:
                    raise BackendUnavailable("ModelScope image reference URL is not public") from exc
                continue
            raise BackendUnavailable("ModelScope image reference is not supported")

        if data_urls and remote_urls:
            raise BackendUnavailable("ModelScope mixed local and remote image references are not supported")

        values = data_urls or remote_urls
        field = "image" if data_urls else "image_url"
        multi_reference_model = target.model == "Qwen/Qwen-Image-Edit-2511"
        return {field: values if multi_reference_model or len(values) > 1 else values[0]}

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("MODELSCOPE_API_KEY is not configured")
        if not await quota.available():
            raise RateLimited("local ModelScope quota is exhausted")

        reference_payload = self._reference_payload(req, target)
        payload: dict[str, Any] = {
            "model": target.model,
            "prompt": req.prompt,
            "n": 1,
        }
        if reference_payload:
            payload.update(reference_payload)
        else:
            payload["size"] = req.size

        base_url = runtime.base_url
        async with provider_client() as client:
            try:
                submit = await request_with_provider_errors(
                    client,
                    "POST",
                    f"{base_url}/v1/images/generations",
                    provider="ModelScope",
                    operation="submit",
                    headers={
                        "Authorization": f"Bearer {runtime.api_key}",
                        "Content-Type": "application/json",
                        "X-ModelScope-Async-Mode": "true",
                    },
                    json=payload,
                )
            except RateLimited as exc:
                await quota.mark_exhausted()
                raise RateLimited("ModelScope remote quota is exhausted") from exc

            data = require_mapping(
                safe_json_response(submit, provider="ModelScope", operation="submit"),
                provider="ModelScope",
                operation="submit",
            )

            task_id = data.get("task_id")
            if not task_id:
                raise BackendUnavailable("ModelScope 提交响应缺少 task_id")

            await quota.consume_one()
            log.info("ModelScope task submitted: model=%s task_id=%s remaining=%s", target.model, task_id, quota.remaining)

            deadline = time.time() + C.MAX_POLL_TIME
            while time.time() < deadline:
                await asyncio.sleep(C.POLL_INTERVAL)
                try:
                    poll = await request_with_provider_errors(
                        client,
                        "GET",
                        f"{base_url}/v1/tasks/{task_id}",
                        provider="ModelScope",
                        operation="poll",
                        headers={
                            "Authorization": f"Bearer {runtime.api_key}",
                            "X-ModelScope-Task-Type": C.MODELSCOPE_POLL_TASK_TYPE,
                        },
                        timeout=20,
                    )
                except RateLimited as exc:
                    await quota.mark_exhausted()
                    raise RateLimited("ModelScope task polling rate limited") from exc

                task = require_mapping(
                    safe_json_response(poll, provider="ModelScope", operation="poll"),
                    provider="ModelScope",
                    operation="poll",
                )
                status = task.get("task_status", "")
                if status == "SUCCEED":
                    images = task.get("output_images") or []
                    if images:
                        return openai_image_response(url=images[0])
                    raise BackendUnavailable("ModelScope 任务成功但未返回图片")
                if status == "FAILED":
                    raise BackendUnavailable("ModelScope 任务失败")

        raise BackendUnavailable(f"ModelScope polling timed out after {C.MAX_POLL_TIME}s")

    def health(self) -> dict[str, Any]:
        return {
            "configured": bool(resolve_provider_runtime_config(self.name).api_key),
            "remaining_local_counter": quota.remaining,
            "daily_limit_local_counter": C.MODELSCOPE_DAILY_LIMIT,
        }
