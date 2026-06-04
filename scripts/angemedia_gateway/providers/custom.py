"""自定义 Provider 运行时。"""
from __future__ import annotations

from typing import Any

import httpx

from .. import config as C
from ..schemas import ImageRequest
from ..security import ensure_public_http_url, redact_secret_text
from .base import BackendUnavailable, RateLimited


async def generate_custom_openai_image(req: ImageRequest, provider: dict[str, Any]) -> dict[str, Any]:
    """调用 OpenAI-compatible 图片接口。

    自定义渠道的最小约定：
    - base_url 指向 OpenAI-compatible v1 根地址；
    - 生成接口为 {base_url}/images/generations；
    - 响应包含 data[0].url 或 data[0].b64_json。
    """
    if not provider.get("enabled"):
        raise BackendUnavailable("自定义渠道已停用")

    try:
        base_url = ensure_public_http_url(str(provider.get("base_url") or "").rstrip("/"))
    except ValueError as exc:
        raise BackendUnavailable(str(exc)) from exc
    api_key = str(provider.get("api_key") or "")
    model = str(provider.get("default_model") or req.model or "")
    if not base_url or not model:
        raise BackendUnavailable("自定义渠道缺少 base_url 或 default_model")

    payload: dict[str, Any] = {
        "model": model,
        "prompt": req.prompt,
        "n": 1,
        "size": req.size,
        "response_format": req.response_format,
    }
    if req.quality:
        payload["quality"] = req.quality
    if req.user:
        payload["user"] = req.user
    if req.negative_prompt:
        payload["negative_prompt"] = req.negative_prompt
    if req.seed is not None:
        payload["seed"] = req.seed

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=C.HTTP_TIMEOUT) as client:
        resp = await client.post(f"{base_url}/images/generations", headers=headers, json=payload)

    if resp.status_code == 401:
        raise BackendUnavailable("自定义渠道鉴权失败")
    if resp.status_code == 429:
        raise RateLimited("自定义渠道限流")
    if resp.status_code != 200:
        raise BackendUnavailable(f"自定义渠道 {resp.status_code}: {redact_secret_text(resp.text[:300])}")

    data = resp.json()
    item = (data.get("data") or [{}])[0]
    if not item.get("url") and not item.get("b64_json"):
        raise BackendUnavailable(f"自定义渠道没有返回图片数据：{data}")
    return data
