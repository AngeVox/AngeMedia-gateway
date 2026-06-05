"""图片 Provider 实现。"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import urllib.parse
from datetime import date
from typing import Any

import httpx

from .. import config as C
from ..media import openai_image_response
from ..schemas import ImageRequest
from .base import BackendUnavailable, ProviderBase, RateLimited, RouteTarget
from .mock import MockImageProvider

log = logging.getLogger("angemedia-gateway")


class LocalQuota:
    """本地 ModelScope 保护性计数器。"""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.day = date.today().isoformat()
        self.remaining = C.MODELSCOPE_DAILY_LIMIT
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(C.QUOTA_FILE.read_text(encoding="utf-8"))
            if data.get("day") == self.day:
                self.remaining = int(data.get("remaining", C.MODELSCOPE_DAILY_LIMIT))
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("quota state is ignored: %s", exc)

    def _save(self) -> None:
        C.QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        C.QUOTA_FILE.write_text(
            json.dumps({"day": self.day, "remaining": self.remaining}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def available(self) -> bool:
        async with self.lock:
            today = date.today().isoformat()
            if today != self.day:
                self.day = today
                self.remaining = C.MODELSCOPE_DAILY_LIMIT
                self._save()
            return self.remaining > 0

    async def consume_one(self) -> None:
        async with self.lock:
            self.remaining = max(0, self.remaining - 1)
            self._save()

    async def mark_exhausted(self) -> None:
        async with self.lock:
            self.remaining = 0
            self._save()


quota = LocalQuota()


def parse_size(size: str) -> tuple[int, int]:
    try:
        width_text, height_text = size.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid size format: {size!r}, expected WIDTHxHEIGHT") from exc
    if width <= 0 or height <= 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid size: {size!r}")
    return width, height


class SiliconFlowProvider:
    name = "siliconflow"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not C.SILICONFLOW_API_KEY:
            raise BackendUnavailable("SILICONFLOW_API_KEY is not configured")

        image_size = req.size if req.size in C.KOLORS_SIZES else "1024x1024"
        payload = {
            "model": target.model,
            "prompt": req.prompt,
            "image_size": image_size,
            "batch_size": 1,
            "num_inference_steps": 20,
            "guidance_scale": 7.5,
        }

        async with httpx.AsyncClient(timeout=C.HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://api.siliconflow.cn/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {C.SILICONFLOW_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if resp.status_code == 401:
            raise BackendUnavailable("SiliconFlow API key is invalid or expired")
        if resp.status_code == 429:
            raise RateLimited("SiliconFlow rate limited")
        if resp.status_code != 200:
            raise BackendUnavailable(f"SiliconFlow {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        images = data.get("images") or []
        if not images or not images[0].get("url"):
            raise BackendUnavailable(f"SiliconFlow returned no image URL: {data}")
        return openai_image_response(url=images[0]["url"])

    def health(self) -> str:
        return "configured" if C.SILICONFLOW_API_KEY else "not_configured"


class ModelScopeProvider:
    name = "modelscope"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not C.MODELSCOPE_API_KEY:
            raise BackendUnavailable("MODELSCOPE_API_KEY is not configured")
        if not await quota.available():
            raise RateLimited("local ModelScope quota is exhausted")

        base_url = "https://api-inference.modelscope.cn"
        async with httpx.AsyncClient(timeout=C.HTTP_TIMEOUT) as client:
            submit = await client.post(
                f"{base_url}/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {C.MODELSCOPE_API_KEY}",
                    "Content-Type": "application/json",
                    "X-ModelScope-Async-Mode": "true",
                    "X-ModelScope-Task-Type": C.MODELSCOPE_SUBMIT_TASK_TYPE,
                },
                json={"model": target.model, "prompt": req.prompt, "n": 1},
            )

            if submit.status_code == 429:
                await quota.mark_exhausted()
                raise RateLimited("ModelScope remote quota is exhausted")
            if submit.status_code != 200:
                raise BackendUnavailable(f"ModelScope submit {submit.status_code}: {submit.text[:300]}")

            try:
                data = submit.json()
            except Exception as exc:
                raise BackendUnavailable(f"ModelScope returned non-JSON submit response: {submit.text[:300]}") from exc

            task_id = data.get("task_id")
            if not task_id:
                raise BackendUnavailable(f"ModelScope submit response has no task_id: {data}")

            await quota.consume_one()
            log.info("ModelScope task submitted: model=%s task_id=%s remaining=%s", target.model, task_id, quota.remaining)

            deadline = time.time() + C.MAX_POLL_TIME
            while time.time() < deadline:
                await asyncio.sleep(C.POLL_INTERVAL)
                poll = await client.get(
                    f"{base_url}/v1/tasks/{task_id}",
                    headers={
                        "Authorization": f"Bearer {C.MODELSCOPE_API_KEY}",
                        "X-ModelScope-Task-Type": C.MODELSCOPE_POLL_TASK_TYPE,
                    },
                    timeout=20,
                )
                if poll.status_code == 429:
                    await quota.mark_exhausted()
                    raise RateLimited("ModelScope task polling rate limited")
                if poll.status_code != 200:
                    raise BackendUnavailable(f"ModelScope poll {poll.status_code}: {poll.text[:300]}")

                task = poll.json()
                status = task.get("task_status", "")
                if status == "SUCCEED":
                    images = task.get("output_images") or []
                    if images:
                        return openai_image_response(url=images[0])
                    raise BackendUnavailable(f"ModelScope task succeeded without output_images: {task}")
                if status == "FAILED":
                    raise BackendUnavailable(f"ModelScope task failed: {task}")

        raise BackendUnavailable(f"ModelScope polling timed out after {C.MAX_POLL_TIME}s")

    def health(self) -> dict[str, Any]:
        return {
            "configured": bool(C.MODELSCOPE_API_KEY),
            "remaining_local_counter": quota.remaining,
            "daily_limit_local_counter": C.MODELSCOPE_DAILY_LIMIT,
        }


class PollinationsProvider:
    name = "pollinations"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        width, height = parse_size(req.size)

        if C.POLLINATIONS_API_KEY:
            payload: dict[str, Any] = {
                "prompt": req.prompt,
                "model": target.model or C.POLLINATIONS_DEFAULT_MODEL,
                "n": 1,
                "size": f"{width}x{height}",
                "response_format": req.response_format,
            }
            if req.safe is not None:
                payload["safe"] = req.safe
            async with httpx.AsyncClient(timeout=C.HTTP_TIMEOUT) as client:
                resp = await client.post(
                    "https://gen.pollinations.ai/v1/images/generations",
                    headers={
                        "Authorization": f"Bearer {C.POLLINATIONS_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            if resp.status_code == 429:
                raise RateLimited("Pollinations rate limited")
            if resp.status_code != 200:
                raise BackendUnavailable(f"Pollinations {resp.status_code}: {resp.text[:300]}")
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

        async with httpx.AsyncClient(follow_redirects=True, timeout=C.HTTP_TIMEOUT) as client:
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
        return "configured_key" if C.POLLINATIONS_API_KEY else "legacy_public_endpoint"


class OpenAICompatibleImageProvider:
    name = "openai_image"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not C.OPENAI_IMAGE_API_KEY:
            raise BackendUnavailable("OPENAI_IMAGE_API_KEY / OPENAI_API_KEY is not configured")

        payload: dict[str, Any] = {
            "model": target.model,
            "prompt": req.prompt,
            "n": 1,
            "size": req.size,
            "response_format": req.response_format,
        }
        if req.quality:
            payload["quality"] = req.quality
        if req.user:
            payload["user"] = req.user

        async with httpx.AsyncClient(timeout=C.HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{C.OPENAI_IMAGE_BASE_URL}/images/generations",
                headers={
                    "Authorization": f"Bearer {C.OPENAI_IMAGE_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if resp.status_code == 401:
            raise BackendUnavailable("OpenAI-compatible image provider auth failed")
        if resp.status_code == 429:
            raise RateLimited("OpenAI-compatible image provider rate limited")
        if resp.status_code != 200:
            raise BackendUnavailable(f"OpenAI-compatible image provider {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        item = (data.get("data") or [{}])[0]
        if not item.get("url") and not item.get("b64_json"):
            raise BackendUnavailable(f"OpenAI-compatible image provider returned no image data: {data}")
        return data

    def health(self) -> str:
        return "configured" if C.OPENAI_IMAGE_API_KEY else "not_configured"


AGNES_IMAGE_EXTRA_ALLOWLIST = {
    "image", "images", "mask", "strength", "tags", "extra_body", "control_image", "reference_image",
    "reference_images", "input_image", "input_images", "init_image", "mask_image", "edit_mode", "mode",
    "width", "height", "steps", "guidance_scale", "cfg_scale", "sampler", "scheduler",
}


def extract_extra_image_options(req: ImageRequest) -> dict[str, Any]:
    extras = getattr(req, "model_extra", {}) or {}
    return {key: value for key, value in extras.items() if key in AGNES_IMAGE_EXTRA_ALLOWLIST and value is not None}


def has_agnes_image_input(payload: dict[str, Any]) -> bool:
    direct_keys = ("image", "images", "input_image", "input_images", "init_image", "mask", "mask_image", "control_image", "reference_image", "reference_images")
    if any(payload.get(key) for key in direct_keys):
        return True
    extra_body = payload.get("extra_body")
    return isinstance(extra_body, dict) and any(extra_body.get(key) for key in direct_keys)


class AgnesImageProvider:
    name = "agnes_image"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not C.AGNES_API_KEY:
            raise BackendUnavailable("AGNES_API_KEY is not configured")

        payload: dict[str, Any] = {
            "model": target.model,
            "prompt": req.prompt,
            "n": req.n,
            "size": req.size,
        }
        extra_body: dict[str, Any] = {"response_format": req.response_format}
        if req.quality:
            extra_body["quality"] = req.quality
        if req.user:
            extra_body["user"] = req.user
        if req.safe is not None:
            extra_body["safe"] = req.safe
        if req.negative_prompt:
            extra_body["negative_prompt"] = req.negative_prompt
        if req.seed is not None:
            extra_body["seed"] = req.seed
        payload["extra_body"] = extra_body

        extra_options = extract_extra_image_options(req)
        user_extra_body = extra_options.pop("extra_body", None)
        payload.update(extra_options)
        if isinstance(user_extra_body, dict):
            payload.setdefault("extra_body", {}).update(user_extra_body)

        if has_agnes_image_input(payload):
            tags = payload.setdefault("tags", [])
            if isinstance(tags, str):
                tags = [tags]
                payload["tags"] = tags
            if isinstance(tags, list) and "img2img" not in tags:
                tags.append("img2img")

        async with httpx.AsyncClient(timeout=C.HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{C.AGNES_BASE_URL}/images/generations",
                headers={
                    "Authorization": f"Bearer {C.AGNES_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if resp.status_code == 401:
            raise BackendUnavailable("Agnes AI API key is invalid or expired")
        if resp.status_code == 429:
            raise RateLimited("Agnes AI image provider rate limited")
        if resp.status_code not in (200, 201):
            raise BackendUnavailable(f"Agnes AI image provider {resp.status_code}: {resp.text[:300]}")

        return normalize_image_response(resp.json())

    def health(self) -> str:
        return "configured" if C.AGNES_API_KEY else "not_configured"


def normalize_image_response(data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("data"), list) and data["data"]:
        item = data["data"][0]
        if isinstance(item, dict):
            if item.get("url") or item.get("b64_json"):
                return data
            for key in ("image_url", "output_url"):
                if item.get(key):
                    return openai_image_response(url=item[key])
        if isinstance(item, str):
            return openai_image_response(url=item)

    for key in ("url", "image_url", "output_url"):
        if isinstance(data.get(key), str):
            return openai_image_response(url=data[key])

    images = data.get("images") or data.get("output_images") or []
    if images:
        first = images[0]
        if isinstance(first, str):
            return openai_image_response(url=first)
        if isinstance(first, dict):
            for key in ("url", "image_url", "output_url", "b64_json"):
                if first.get(key):
                    if key == "b64_json":
                        return openai_image_response(b64_json=first[key])
                    return openai_image_response(url=first[key])

    raise BackendUnavailable(f"图片接口没有返回可识别的图片地址：{data}")


def build_providers() -> dict[str, ProviderBase]:
    return {
        "siliconflow": SiliconFlowProvider(),
        "modelscope": ModelScopeProvider(),
        "pollinations": PollinationsProvider(),
        "openai_image": OpenAICompatibleImageProvider(),
        "agnes_image": AgnesImageProvider(),
        "mock": MockImageProvider(),
    }
