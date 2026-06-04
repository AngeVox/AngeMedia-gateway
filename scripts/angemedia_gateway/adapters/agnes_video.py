"""Agnes AI 视频适配器。 

这个文件只处理视频接口，主网关保持图片生成为主。
新增视频模型时，优先改这里，不要把视频逻辑继续堆进 gateway.py。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ..schemas import VideoRequest


class AgnesVideoError(RuntimeError):
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
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_poll_time = max_poll_time
        self.poll_interval = poll_interval

    def health(self) -> dict[str, Any]:
        return {
            "configured": bool(self.api_key),
            "base_url": self.base_url,
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
        if not self.api_key:
            raise AgnesVideoError("AGNES_API_KEY is not configured")

        payload = self.build_payload(req)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/videos",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if resp.status_code == 401:
            raise AgnesVideoError("Agnes AI API key is invalid or expired")
        if resp.status_code == 429:
            raise AgnesVideoError("Agnes AI video provider rate limited")
        if resp.status_code not in (200, 201, 202):
            raise AgnesVideoError(f"Agnes AI submit {resp.status_code}: {resp.text[:300]}")

        return self.normalize_submit(resp.json())

    async def poll_task(self, task_id: str) -> dict[str, Any]:
        if not self.api_key:
            raise AgnesVideoError("AGNES_API_KEY is not configured")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/videos/{task_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )

        if resp.status_code == 401:
            raise AgnesVideoError("Agnes AI API key is invalid or expired")
        if resp.status_code not in (200, 201, 202):
            raise AgnesVideoError(f"Agnes AI poll {resp.status_code}: {resp.text[:300]}")

        return self.normalize_poll(resp.json(), task_id)

    async def generate_video(self, req: VideoRequest) -> dict[str, Any]:
        submit = await self.submit_task(req)
        task_id = submit.get("task_id") or submit.get("id")
        if not task_id:
            raise AgnesVideoError(f"Agnes AI submit response has no task_id: {submit}")

        deadline = time.time() + self.max_poll_time
        while time.time() < deadline:
            await asyncio.sleep(self.poll_interval)
            result = await self.poll_task(task_id)
            status = str(result.get("status", "")).lower()
            if status in {"completed", "succeeded", "success", "done"}:
                return result
            if status in {"failed", "error", "cancelled"}:
                raise AgnesVideoError(f"Agnes AI video task failed: {result}")

        raise AgnesVideoError(f"Agnes AI video polling timed out after {self.max_poll_time}s")

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
