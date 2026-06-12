"""ModelScope image adapter."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ... import config as C
from ...media import openai_image_response
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable, RateLimited
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from .quota import quota

log = logging.getLogger("angemedia-gateway")


class ModelScopeProvider:
    name = "modelscope"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not C.MODELSCOPE_API_KEY:
            raise BackendUnavailable("MODELSCOPE_API_KEY is not configured")
        if not await quota.available():
            raise RateLimited("local ModelScope quota is exhausted")

        base_url = "https://api-inference.modelscope.cn"
        async with provider_client() as client:
            try:
                submit = await request_with_provider_errors(
                    client,
                    "POST",
                    f"{base_url}/v1/images/generations",
                    provider="ModelScope",
                    operation="submit",
                    headers={
                        "Authorization": f"Bearer {C.MODELSCOPE_API_KEY}",
                        "Content-Type": "application/json",
                        "X-ModelScope-Async-Mode": "true",
                        "X-ModelScope-Task-Type": C.MODELSCOPE_SUBMIT_TASK_TYPE,
                    },
                    json={"model": target.model, "prompt": req.prompt, "n": 1},
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
                            "Authorization": f"Bearer {C.MODELSCOPE_API_KEY}",
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
            "configured": bool(C.MODELSCOPE_API_KEY),
            "remaining_local_counter": quota.remaining,
            "daily_limit_local_counter": C.MODELSCOPE_DAILY_LIMIT,
        }
