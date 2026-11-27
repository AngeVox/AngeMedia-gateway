"""Pollinations 视频协议；只向固定的同源路径发送凭据。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import quote

import httpx

from .. import config as C
from ..media_execution_scope import require_execution_target
from ..pollinations_video import POLLINATIONS_VIDEO_MODEL, PollinationsVideoRequest
from .errors import BackendUnavailable, ProviderError, ProviderProtocolError
from .http import normalize_httpx_error, provider_client, raise_for_provider_status
from .reference_relay import prepare_reference_with_configured_relay
from .runtime_config import resolve_provider_runtime_config


@dataclass(frozen=True)
class VideoReceipt:
    task_id: str | None
    retry_after: int = 5


@dataclass(frozen=True)
class VideoObservation:
    status: str
    retry_after: int


def _retry_after(response):
    value = response.headers.get("retry-after", "")
    return max(5, min(60, int(value))) if value.isascii() and value.isdigit() and len(value) <= 9 else 5


def _canonical_link(value, expected):
    if not isinstance(value, str):
        raise ProviderProtocolError("Pollinations video returned an invalid job URL")
    # 只校验上游回执，实际查询和下载始终使用本地构造的地址。
    if value != expected and value != httpx.URL(expected).path:
        raise ProviderProtocolError("Pollinations video returned an invalid job URL")


async def _json(response):
    if response.headers.get("content-encoding", "identity") != "identity":
        raise ProviderProtocolError("Pollinations video returned an unsupported receipt encoding")
    payload = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=8192):
        payload.extend(chunk)
        if len(payload) > 65536:
            raise ProviderProtocolError("Pollinations video receipt is too large")
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeError):
        raise ProviderProtocolError("Pollinations video returned invalid JSON") from None
    if not isinstance(data, dict):
        raise ProviderProtocolError("Pollinations video returned invalid JSON")
    return data


async def _save_mp4(response: httpx.Response, destination: Path):
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    length = response.headers.get("content-length")
    if (content_type != "video/mp4" or response.headers.get("content-encoding", "identity") != "identity"
        or (length is not None and (not re.fullmatch(r"[0-9]{1,12}", length)
                                   or int(length) > C.MEDIA_DOWNLOAD_MAX_BYTES))):
        raise ProviderProtocolError("Pollinations video media response is invalid")
    total = 0
    with destination.open("xb") as stream:
        async for chunk in response.aiter_bytes(chunk_size=65536):
            total += len(chunk)
            if total > C.MEDIA_DOWNLOAD_MAX_BYTES:
                raise ProviderProtocolError("Pollinations video exceeds the media limit")
            stream.write(chunk)
    if length is not None and total != int(length):
        raise ProviderProtocolError("Pollinations video media length does not match")
    # 扫描顶层容器边界，避免把上游误标的错误页或截断文件当作成功结果。
    required = {b"ftyp", b"moov", b"mdat"}
    with destination.open("rb") as stream:
        offset = 0
        for _ in range(4096):
            if offset == total:
                if not required:
                    return
                break
            header = stream.read(8)
            if len(header) != 8:
                break
            size, kind = int.from_bytes(header[:4], "big"), header[4:]
            header_size = 8
            if size == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    break
                size, header_size = int.from_bytes(extended, "big"), 16
            elif size == 0:
                size = total - offset
            if size < header_size or offset + size > total:
                break
            if size > header_size and (kind != b"ftyp" or size >= header_size + 8):
                required.discard(kind)
            offset += size
            stream.seek(offset)
    raise ProviderProtocolError("Pollinations video media is incomplete or invalid")


class PollinationsVideoProvider:
    name = "pollinations"

    @asynccontextmanager
    async def _request(self, path, *, operation, statuses=(200,), params=None, submit=False):
        phase = "submit" if submit else "followup"
        require_execution_target(self.name, POLLINATIONS_VIDEO_MODEL, operation=phase)
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("Pollinations video requires a credential", error_category="auth")
        root = runtime.base_url.removesuffix("/v1").rstrip("/")
        url = root + path
        headers = {"Authorization": f"Bearer {runtime.api_key}", "Accept-Encoding": "identity"}
        if submit:
            headers["Prefer"] = "respond-async"
        try:
            async with asyncio.timeout(float(C.HTTP_TIMEOUT) * 3), provider_client(provider_id=self.name) as client:
                async with client.stream("GET", url, headers=headers, params=params, follow_redirects=False) as response:
                    try:
                        raise_for_provider_status(response, provider="Pollinations", operation=operation, ok_statuses=statuses)
                    except ProviderError as exc:
                        exc.retry_after_seconds = _retry_after(response)
                        raise
                    yield response, root
        except TimeoutError:
            raise normalize_httpx_error(httpx.ReadTimeout("deadline exceeded"), provider="Pollinations", operation=operation) from None
        except httpx.HTTPError as exc:
            raise normalize_httpx_error(exc, provider="Pollinations", operation=operation) from None

    async def submit(self, request: PollinationsVideoRequest, destination: Path):
        params = {"model": request.model, "duration": request.seconds, "resolution": request.size}
        references = []
        for value in (request.first_frame, request.last_frame):
            if value:
                require_execution_target(self.name, request.model)
                decision = await prepare_reference_with_configured_relay(self.name, value, model=request.model)
                if "|" in decision.value:
                    raise ProviderProtocolError("Pollinations video reference URL is invalid")
                references.append(decision.value)
        if references:
            params["image"] = "|".join(references)
        prompt_path = quote(request.prompt, safe="")
        if prompt_path in {".", ".."}:
            prompt_path = prompt_path.replace(".", "%2E")
        path = "/video/" + prompt_path
        async with self._request(path, operation="video submit", statuses=(200, 202),
                                 params=params, submit=True) as (response, root):
            if response.status_code == 200:
                await _save_mp4(response, destination)
                return VideoReceipt(None)
            data = await _json(response)
            task_id = data.get("id")
            if (not isinstance(task_id, str) or not re.fullmatch(r"[a-f0-9]{64}", task_id)
                or data.get("status") != "pending"):
                raise ProviderProtocolError("Pollinations video returned an invalid receipt")
            expected = root + self._job_path(task_id)
            _canonical_link(data.get("status_url"), expected)
            if "location" in response.headers:
                _canonical_link(response.headers["location"], expected)
            return VideoReceipt(task_id, _retry_after(response))

    @staticmethod
    def _job_path(task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r"[a-f0-9]{64}", task_id):
            raise ProviderProtocolError("Pollinations video has an invalid task identity")
        return "/v1/jobs/" + task_id

    async def poll(self, task_id):
        path = self._job_path(task_id)
        async with self._request(path, operation="video status") as (response, root):
            data = await _json(response)
            status = data.get("status")
            if data.get("id") != task_id or status not in ("pending", "succeeded", "failed"):
                raise ProviderProtocolError("Pollinations video returned an invalid task status")
            if status == "succeeded":
                _canonical_link(data.get("result_url"), root + path + "/result")
            return VideoObservation(status, _retry_after(response))

    async def retrieve(self, task_id, destination):
        path = self._job_path(task_id) + "/result"
        async with self._request(path, operation="video result") as (response, _):
            await _save_mp4(response, destination)
