"""ModelScope异步图片推理适配器，账户权限与本地保护预算分别处理。"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from urllib.parse import quote, urlsplit
from typing import Any

from ... import config as C
from ...media import openai_image_response
from ...reference_images import collect_image_reference_values
from ...schemas import ImageRequest
from ...security import validate_provider_external_id
from ..base import RouteTarget
from ..catalog.loader import load_provider_catalog
from ..catalog.validation import CatalogOperationValidationError, image_operation_for_request, validate_image_operation_request
from ..errors import BackendUnavailable, ProviderError, ProviderProtocolError, ProviderTaskFailed, ProviderValidationError, RateLimited
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import ResolvedProviderRuntimeConfig, resolve_provider_runtime_config
from ..reference_delivery import DATA_URL, PUBLIC_URL, RELAY_REQUIRED, prepare_builtin_reference
from .quota import quota

log = logging.getLogger("angemedia-gateway")


@dataclass(frozen=True)
class ModelScopeImageTask:
    """只保留已观察状态与作品地址，不携带账户凭据或上游原始响应。"""

    task_id: str
    status: str
    image_url: str | None = None


def _task_id(value: Any) -> str:
    """上游编号按不透明标识校验；路径编码由查询入口负责。"""
    try:
        if not isinstance(value, str) or value in {'.', '..'}:
            raise ValueError
        return validate_provider_external_id(value)
    except ValueError:
        raise ProviderProtocolError('ModelScope 任务编号无效') from None


def _image_task(data: dict[str, Any], task_id: str) -> ModelScopeImageTask:
    """未知非空状态保留为未完成，不借音频等其他操作推断图片状态全集。"""
    # 旧响应可以省略任务号；一旦上游回传，必须与本次查询的已受理任务完全一致。
    if 'task_id' in data and _task_id(data['task_id']) != task_id:
        raise ProviderProtocolError('ModelScope 查询响应与原任务不匹配')
    status = data.get('task_status')
    if not isinstance(status, str) or not status or len(status) > 64 or status != status.strip() or any(ord(c) < 32 or ord(c) == 127 for c in status):
        raise ProviderProtocolError('ModelScope 任务状态无效')
    image_url = None
    if status == 'SUCCEED':
        images = data.get('output_images')
        candidate = images[0] if isinstance(images, list) and images else None
        try:
            if not isinstance(candidate, str) or candidate != candidate.strip() or any(ord(c) < 32 or ord(c) == 127 for c in candidate):
                raise ValueError
            parsed = urlsplit(candidate)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                raise ValueError
        except ValueError:
            raise ProviderProtocolError('ModelScope 任务成功但未返回有效图片地址') from None
        image_url = candidate
    return ModelScopeImageTask(task_id, status, image_url)


class ModelScopeProvider:
    name = "modelscope"

    @staticmethod
    def _reference_payload(req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        references = collect_image_reference_values(req)
        native = getattr(req, 'image_url', None)
        if native is not None:
            values = native if isinstance(native, list) else [native]
            references.extend(value.strip() for value in values if isinstance(value,str) and value.strip())
        if not references:
            return {}

        data_urls: list[str] = []
        remote_urls: list[str] = []
        for reference in references:
            try:
                decision = prepare_builtin_reference("modelscope", reference, model=target.model)
            except ValueError as exc:
                raise BackendUnavailable("ModelScope image reference is not supported") from exc
            if decision.kind == DATA_URL:
                data_urls.append(decision.value)
            elif decision.kind == PUBLIC_URL:
                remote_urls.append(decision.value)
            elif decision.kind == RELAY_REQUIRED:
                raise BackendUnavailable("ModelScope image reference requires a configured relay")
            else:
                raise BackendUnavailable("ModelScope image reference delivery is not supported")

        if data_urls and remote_urls:
            raise BackendUnavailable("ModelScope mixed local and remote image references are not supported")

        values = data_urls or remote_urls
        multi_reference_model = target.model == "Qwen/Qwen-Image-Edit-2511"
        return {"image_url": values if multi_reference_model or len(values) > 1 else values[0]}

    @staticmethod
    def _runtime() -> ResolvedProviderRuntimeConfig:
        runtime = resolve_provider_runtime_config('modelscope')
        if not runtime.api_key:
            raise BackendUnavailable('MODELSCOPE_API_KEY is not configured', error_category='auth')
        return runtime

    async def submit(self, req: ImageRequest, target: RouteTarget) -> str:
        """仅提交一次，返回经校验的任务号；不轮询、不自动重提。"""
        runtime = self._runtime()
        async with provider_client(provider_id=self.name) as client:
            return await self._submit(client, runtime, req, target)

    async def poll(self, task_id: str) -> ModelScopeImageTask:
        """只查询指定任务，不占用本地提交预算、不创建新生成。"""
        task_id = _task_id(task_id)
        runtime = self._runtime()
        async with provider_client(provider_id=self.name) as client:
            return await self._poll(client, runtime, task_id)

    async def _submit(self, client: Any, runtime: ResolvedProviderRuntimeConfig, req: ImageRequest, target: RouteTarget) -> str:
        try:
            payload = build_modelscope_image_payload(req, target)
        except CatalogOperationValidationError as exc:
            raise ProviderValidationError(str(exc)) from None
        if not await quota.try_acquire():
            raise RateLimited('local_budget_exhausted: ModelScope local submission-attempt budget is exhausted', retryable=False, error_category='local_budget')
        try:
            submit = await request_with_provider_errors(
                client, 'POST', f'{runtime.base_url}/v1/images/generations',
                provider='ModelScope', operation='submit',
                headers={'Authorization': f'Bearer {runtime.api_key}', 'Content-Type': 'application/json', 'X-ModelScope-Async-Mode': 'true'},
                json=payload,
            )
        except ProviderError as exc:
            if exc.status_code not in {400, 401, 403, 422, 429}:
                exc.request_committed = True
                exc.retryable = False
                exc.error_category = 'ambiguous_submit'
            raise
        try:
            data = require_mapping(safe_json_response(submit, provider='ModelScope', operation='submit'), provider='ModelScope', operation='submit')
            return _task_id(data.get('task_id'))
        except ProviderError as exc:
            exc.request_committed = True
            exc.retryable = False
            exc.error_category = 'ambiguous_submit'
            raise

    async def _poll(self, client: Any, runtime: ResolvedProviderRuntimeConfig, task_id: str) -> ModelScopeImageTask:
        poll = await request_with_provider_errors(
            client, 'GET', f'{runtime.base_url}/v1/tasks/{quote(task_id, safe="")}',
            provider='ModelScope', operation='poll',
            headers={'Authorization': f'Bearer {runtime.api_key}', 'X-ModelScope-Task-Type': C.MODELSCOPE_POLL_TASK_TYPE},
            timeout=20,
        )
        task = require_mapping(safe_json_response(poll, provider='ModelScope', operation='poll'), provider='ModelScope', operation='poll')
        return _image_task(task, task_id)

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        """兼容原同步调用，共用单次协议与一个HTTP连接池；这里不提供崩溃恢复。"""
        runtime = self._runtime()
        async with provider_client(provider_id=self.name) as client:
            task_id = await self._submit(client, runtime, req, target)
            log.info('ModelScope task submitted: model=%s', target.model)
            deadline = time.monotonic() + C.MAX_POLL_TIME
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(max(C.POLL_INTERVAL, 0), remaining))
                if time.monotonic() >= deadline:
                    break
                try:
                    task = await self._poll(client, runtime, task_id)
                except ProviderError as exc:
                    exc.request_committed = True
                    exc.retryable = False
                    raise
                if task.status == 'SUCCEED':
                    return openai_image_response(url=task.image_url)
                if task.status == 'FAILED':
                    error = ProviderTaskFailed('ModelScope 任务失败', provider_status=task.status)
                    error.request_committed = True
                    raise error
        error = BackendUnavailable(f'ModelScope polling timed out after {C.MAX_POLL_TIME}s')
        error.request_committed = True
        raise error

    def health(self) -> dict[str, Any]:
        budget = quota.snapshot()
        return {
            "configured": bool(resolve_provider_runtime_config(self.name).api_key),
            "local_budget": budget.as_public_dict(),
            # 兼容旧消费者，旧字段也仅表示本地保护预算。
            "remaining_local_counter": budget.remaining,
            "daily_limit_local_counter": budget.limit,
        }


def build_modelscope_image_payload(req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
    """目录声明与真实请求共用字段映射；未知型号只发送显式公共参数。"""
    catalog = load_provider_catalog()
    if target.provider != 'modelscope':
        raise CatalogOperationValidationError('ModelScope received an incompatible provider target')
    validate_image_operation_request(req, [target], catalog=catalog)
    model = next((item for item in catalog.models if item.provider == target.provider and item.provider_model == target.model), None)
    payload: dict[str, Any] = {'model': target.model, 'n': 1}
    if model is not None:
        operation = model.operations[image_operation_for_request(req, model)]
        for name, spec in operation.params.items():
            value = getattr(req, name, None)
            if spec.provider_field and value is not None:
                payload[spec.provider_field] = value
    else:
        payload.update(prompt=req.prompt, size=req.size)
        for name in ('negative_prompt', 'seed', 'steps'):
            value = getattr(req, name)
            if value is not None:
                payload[name] = value
    references = ModelScopeProvider._reference_payload(req, target)
    if references:
        payload.update(references)
    return payload
