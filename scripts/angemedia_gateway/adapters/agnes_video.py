"""Agnes AI 视频适配器。 

这个文件只处理视频接口，主网关保持图片生成为主。
新增视频模型时，优先改这里，不要把视频逻辑继续堆进 gateway.py。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from ..providers.errors import (
    BackendUnavailable,
    ProviderAuthError,
    ProviderProtocolError,
    ProviderTaskFailed,
    ProviderTimeout,
)
from ..providers.http import provider_client, request_with_provider_errors, safe_json_response
from ..providers.parsers import require_mapping
from ..providers.runtime_config import ResolvedProviderRuntimeConfig
from ..schemas import VideoRequest


class AgnesVideoError(BackendUnavailable):
    """Agnes 视频接口错误。"""



class AgnesVideoProvider:
    """Agnes AI 视频提供者。"""

    name = "agnes_video"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float = 60,
        max_poll_time: int = 600,
        poll_interval: float = 5,
        runtime_config_resolver: Callable[[str], ResolvedProviderRuntimeConfig] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_poll_time = max_poll_time
        self.poll_interval = poll_interval
        self.runtime_config_resolver = runtime_config_resolver

    def _credentials(self) -> tuple[str, str]:
        if self.runtime_config_resolver is None:
            return self.api_key, self.base_url
        runtime = self.runtime_config_resolver(self.name)
        return runtime.api_key, runtime.base_url

    def health(self) -> dict[str, Any]:
        api_key, base_url = self._credentials()
        return {
            "configured": bool(api_key),
            "base_url": base_url,
            "max_poll_time": self.max_poll_time,
            "poll_interval": self.poll_interval,
        }

    def build_payload(self, req: VideoRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": req.model,
            "prompt": req.prompt,
            "height": req.height,
            "width": req.width,
            "num_frames": req.num_frames,
            "frame_rate": req.frame_rate,
        }

        if req.image:
            payload["image"] = req.image

        if req.images:
            payload.setdefault("extra_body", {})["image"] = req.images
            if req.mode:
                payload["extra_body"]["mode"] = req.mode

        if req.negative_prompt:
            payload["negative_prompt"] = req.negative_prompt
        if req.seed is not None:
            payload["seed"] = req.seed
        if req.num_inference_steps is not None:
            payload["num_inference_steps"] = req.num_inference_steps

        if req.extra_body:
            payload.setdefault("extra_body", {}).update(req.extra_body)

        return payload

    async def submit_task(self, req: VideoRequest) -> dict[str, Any]:
        api_key, base_url = self._credentials()
        if not api_key:
            raise ProviderAuthError("agnes_video submit failed: auth")

        payload = self.build_payload(req)
        data = await self._request_json(
            "POST",
            f"{base_url}/videos",
            operation="submit",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

        return self.normalize_submit(data)

    async def poll_task(self, task_id: str) -> dict[str, Any]:
        api_key, base_url = self._credentials()
        if not api_key:
            raise ProviderAuthError("agnes_video poll failed: auth")

        data = await self._request_json(
            "GET",
            f"{base_url}/videos/{task_id}",
            operation="poll",
            headers={"Authorization": f"Bearer {api_key}"},
        )

        return self.normalize_poll(data, task_id)

    async def generate_video(self, req: VideoRequest) -> dict[str, Any]:
        submit = await self.submit_task(req)
        task_id = submit.get("task_id") or submit.get("id")
        if not task_id:
            raise ProviderProtocolError("agnes_video submit failed: missing task_id")

        deadline = time.time() + self.max_poll_time
        while time.time() < deadline:
            await asyncio.sleep(self.poll_interval)
            result = await self.poll_task(task_id)
            status = str(result.get("status", "")).lower()
            if status in {"completed", "succeeded", "success", "done"}:
                return result
            if status in {"failed", "error", "cancelled"}:
                safe_status = str(status or "unknown")[:64]
                raise ProviderTaskFailed(f"agnes_video task failed: {safe_status}")

        raise ProviderTimeout("agnes_video poll failed: timeout")

    async def _request_json(self, method: str, url: str, *, operation: str, **kwargs: Any) -> dict[str, Any]:
        async with provider_client(timeout=self.timeout) as client:
            response = await request_with_provider_errors(
                client,
                method,
                url,
                provider=self.name,
                operation=operation,
                ok_statuses=(200, 201, 202),
                **kwargs,
            )
        return require_mapping(
            safe_json_response(response, provider=self.name, operation=operation),
            provider=self.name,
            operation=operation,
        )

    @staticmethod
    def normalize_submit(data: dict[str, Any]) -> dict[str, Any]:
        task_id = data.get("task_id") or data.get("id")
        if task_id and "task_id" not in data:
            data = {**data, "task_id": task_id}
        data.setdefault("status", "queued")
        return data

    @staticmethod
    def normalize_poll(data: dict[str, Any], task_id: str) -> dict[str, Any]:
        data = dict(data)
        data.setdefault("task_id", task_id)
        video_url = data.get("video_url") or data.get("remixed_from_video_id") or data.get("url") or data.get("output_url")
        # ⚠️ Agnes API 命名不规范：remixed_from_video_id 实际返回的是完整视频 URL 而非视频 ID
        if video_url:
            data["video_url"] = video_url
            # 有些 Agnes 响应已经给出视频地址，但不一定带标准完成状态。
            # 同步等待时如果没有状态，这里补成 completed，避免拿到地址后仍然轮询到超时。
            if not data.get("status"):
                data["status"] = "completed"
        return data
