"""Agnes AI 视频适配器。 

这个文件只处理视频接口，主网关保持图片生成为主。
新增视频模型时，优先改这里，不要把视频逻辑继续堆进 gateway.py。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx

from ..providers.errors import (
    BackendUnavailable,
    ProviderAuthError,
    ProviderProtocolError,
    ProviderQueueFull,
    ProviderTaskFailed,
    ProviderTimeout,
)
from ..providers.http import normalize_http_status, provider_client, request_with_provider_errors, safe_json_response
from ..providers.parsers import require_mapping
from ..providers.runtime_config import ResolvedProviderRuntimeConfig
from ..media_execution_scope import current_execution_scope, require_execution_target
from ..reference_images import validate_gateway_image_reference
from ..schemas import VideoRequest
from ..security import validate_provider_external_id, validate_task_id
from ..video_models import agnes_video_profile, is_agnes_video_modern
from ..providers.reference_delivery import BASE64, PUBLIC_URL, RELAY_REQUIRED, prepare_builtin_reference
from ..providers.reference_relay import prepare_reference_with_configured_relay


def _submit_task_id(data: dict[str, Any]) -> Any:
    return data.get("video_id") or data.get("task_id") or data.get("id")


def _is_submit_queue_full(response: httpx.Response) -> bool:
    """只采信 Agnes 提交接口明确返回的业务码；有任务号时保留提交未确认。"""
    try:
        body = safe_json_response(response, provider="agnes_video", operation="submit")
    except ProviderProtocolError:
        return False
    if not isinstance(body, dict) or body.get("code") != "video_queue_full" or _submit_task_id(body):
        return False
    data = body.get("data")
    return not (isinstance(data, dict) and _submit_task_id(data))


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
        scope = current_execution_scope()
        if scope is not None:
            runtime = scope.runtime(self.name)
            return runtime.api_key, runtime.base_url
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

    @staticmethod
    def _materialize_image_payload(value: str | None) -> str:
        safe_reference = validate_gateway_image_reference(value)
        decision = prepare_builtin_reference("agnes_video", safe_reference, model="agnes-video-v2.0")
        if decision.kind != BASE64:
            raise ValueError("reference image cannot be delivered as base64")
        return decision.value

    def build_payload(self, req: VideoRequest) -> dict[str, Any]:
        if is_agnes_video_modern(req.model):
            return self._build_modern_payload(req)

        payload: dict[str, Any] = {
            "model": req.model,
            "prompt": req.prompt,
            "height": req.height,
            "width": req.width,
            "num_frames": req.num_frames,
            "frame_rate": req.frame_rate,
        }

        if req.image:
            payload["image"] = self._materialize_image_payload(req.image)

        if req.images:
            payload.setdefault("extra_body", {})["image"] = [
                self._materialize_image_payload(image) for image in req.images
            ]
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

    @staticmethod
    def _public_media_url(value: str | None, model: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Agnes Video 2.5 reference URL is required")
        decision = prepare_builtin_reference("agnes_video", value, model=model)
        if decision.kind == PUBLIC_URL:
            return decision.value
        if decision.kind == RELAY_REQUIRED:
            raise ValueError("Agnes Video 2.5 local reference requires a configured reference relay")
        raise ValueError("Agnes Video 2.5 reference delivery is not supported")

    def _build_modern_payload(self, req: VideoRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": agnes_video_profile(req.model).model,
            "prompt": req.prompt,
            "seconds": str(req.seconds or "5"),
            "mode": str(req.mode or "text"),
            "size": str(req.size or "720P"),
            "aspect_ratio": str(req.aspect_ratio or "16:9"),
        }
        if req.seed is not None:
            payload["seed"] = req.seed

        if req.mode == "keyframe":
            if req.first_frame:
                payload["first_frame"] = self._public_media_url(req.first_frame, req.model)
            if req.last_frame:
                payload["last_frame"] = self._public_media_url(req.last_frame, req.model)
        elif req.mode == "reference":
            references = ([req.image] if req.image else []) + list(req.images or [])
            if references:
                payload["images"] = [self._public_media_url(item, req.model) for item in references]
            if req.audios:
                payload["audios"] = list(req.audios)
            if req.videos:
                payload["videos"] = [item.model_dump() for item in req.videos]

        return payload

    async def _prepare_runtime_request(self, req: VideoRequest) -> VideoRequest:
        if not is_agnes_video_modern(req.model):
            return req
        updates: dict[str, Any] = {}
        for field in ("first_frame", "last_frame", "image", "images"):
            current = getattr(req, field, None)
            if current is None:
                continue
            values = current if isinstance(current, list) else [current]
            prepared: list[str] = []
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    continue
                decision = await prepare_reference_with_configured_relay(
                    self.name, value, model=req.model
                )
                if decision.kind != PUBLIC_URL:
                    raise ValueError("Agnes Video 2.5 reference could not be published as a public URL")
                prepared.append(decision.value)
            if isinstance(current, list):
                updates[field] = prepared
            elif prepared:
                updates[field] = prepared[0]
        return req.model_copy(update=updates) if updates else req

    async def submit_task(self, req: VideoRequest) -> dict[str, Any]:
        api_key, base_url = self._credentials()
        if not api_key:
            raise ProviderAuthError("agnes_video submit failed: auth")

        req = await self._prepare_runtime_request(req)
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

    async def poll_task(self, task_id: str, model_name: str | None = None) -> dict[str, Any]:
        require_execution_target('agnes_video', model_name, operation='followup')
        api_key, base_url = self._credentials()
        if not api_key:
            raise ProviderAuthError("agnes_video poll failed: auth")

        external_id = validate_provider_external_id(task_id)
        api_root = base_url[:-3] if base_url.endswith("/v1") else base_url
        params = {"video_id": external_id}
        if is_agnes_video_modern(model_name):
            params["model_name"] = agnes_video_profile(model_name).model
        try:
            data = await self._request_json(
                "GET",
                f"{api_root}/agnesapi",
                operation="poll",
                params=params,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except BackendUnavailable as exc:
            if exc.status_code not in {400, 404, 405, 422}:
                raise
            try:
                legacy_task_id = validate_task_id(external_id)
            except ValueError:
                raise exc from None
            data = await self._request_json(
                "GET",
                f"{base_url}/videos/{legacy_task_id}",
                operation="poll_legacy",
                headers={"Authorization": f"Bearer {api_key}"},
            )

        return self.normalize_poll(data, external_id)

    async def generate_video(self, req: VideoRequest) -> dict[str, Any]:
        submit = await self.submit_task(req)
        task_id = submit.get("task_id") or submit.get("id")
        if not task_id:
            raise ProviderProtocolError("agnes_video submit failed: missing task_id")

        return await self.wait_for_video(task_id, model_name=req.model)

    async def wait_for_video(self, task_id: str, *, model_name: str) -> dict[str, Any]:
        """等待已受理的原任务；供旧便捷方法和有来源的即时入口共用，不再次提交。"""
        task_id = validate_provider_external_id(task_id)

        deadline = time.time() + self.max_poll_time
        while time.time() < deadline:
            await asyncio.sleep(self.poll_interval)
            require_execution_target('agnes_video', model_name, operation='followup')
            result = await self.poll_task(task_id, model_name=model_name)
            status = str(result.get("status", "")).lower()
            if status in {"completed", "succeeded", "success", "done"}:
                return result
            if status in {"failed", "error", "cancelled"}:
                safe_status = str(status or "unknown")[:64]
                raise ProviderTaskFailed(f"agnes_video task failed: {safe_status}", provider_status=safe_status)

        raise ProviderTimeout("agnes_video poll failed: timeout")

    async def _request_json(self, method: str, url: str, *, operation: str, **kwargs: Any) -> dict[str, Any]:
        if operation == "submit":
            # 这里只等任务号。单进程本地队列不能把整个生成时限耗在提交回执上。
            limit = min(self.timeout, 45.0)
            try:
                async with asyncio.timeout(limit):
                    return await self._read_json(method, url, operation=operation, timeout=limit, **kwargs)
            except TimeoutError:
                raise ProviderTimeout("agnes_video submit failed: timeout") from None
        return await self._read_json(method, url, operation=operation, timeout=self.timeout, **kwargs)

    async def _read_json(self, method: str, url: str, *, operation: str, timeout: float, **kwargs: Any) -> dict[str, Any]:
        # 仅提交阶段的 503 需要先读取业务码，再归类为失败。
        statuses = (200, 201, 202, 503) if operation == "submit" else (200, 201, 202)
        async with provider_client(timeout=timeout, provider_id=self.name) as client:
            response = await request_with_provider_errors(
                client,
                method,
                url,
                provider=self.name,
                operation=operation,
                ok_statuses=statuses,
                **kwargs,
            )
        if operation == "submit" and response.status_code == 503:
            if _is_submit_queue_full(response):
                raise ProviderQueueFull("Agnes video queue is full", status_code=response.status_code)
            raise normalize_http_status(response, provider=self.name, operation=operation)
        return require_mapping(
            safe_json_response(response, provider=self.name, operation=operation),
            provider=self.name,
            operation=operation,
        )

    @staticmethod
    def normalize_submit(data: dict[str, Any]) -> dict[str, Any]:
        task_id = _submit_task_id(data)
        normalized: dict[str, Any] = {"status": str(data.get("status") or "queued")[:64]}
        if task_id:
            normalized["task_id"] = str(task_id)
        return normalized

    @staticmethod
    def normalize_poll(data: dict[str, Any], task_id: str) -> dict[str, Any]:
        data = dict(data)
        data.setdefault("task_id", task_id)
        metadata = data.get("metadata")
        metadata_url = metadata.get("url") if isinstance(metadata, dict) else None
        video_url = (
            data.get("video_url")
            or data.get("remixed_from_video_id")
            or data.get("url")
            or data.get("output_url")
            or metadata_url
        )
        # ⚠️ Agnes API 命名不规范：remixed_from_video_id 实际返回的是完整视频 URL 而非视频 ID
        if video_url:
            data["video_url"] = video_url
            # 有些 Agnes 响应已经给出视频地址，但不一定带标准完成状态。
            # 同步等待时如果没有状态，这里补成 completed，避免拿到地址后仍然轮询到超时。
            if not data.get("status"):
                data["status"] = "completed"
        return data
