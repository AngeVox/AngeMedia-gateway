#!/usr/bin/env python3
"""
Image Proxy Gateway v0.2
- 对外提供兼容 OpenAI 图片生成格式的接口：POST /v1/images/generations
- 图片网关保持轻量；Agnes 视频能力已拆到 adapters/agnes_video.py
- 默认内置三层图片渠道：硅基流动 Kolors → 魔搭 Qwen/FLUX/Z-Image → Pollinations 兜底
- 使用渠道注册表结构，后续可以继续接入即梦、GPT 图片模型或其他图片服务
- 可选接入兼容 OpenAI 图片接口的付费渠道，默认别名为 gpt-image-2
- 本地保存魔搭图片额度保护计数，每天自动重置
- 可选配置网关访问密钥，避免局域网或公网被他人滥用
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict

from adapters.agnes_video import AgnesVideoError, AgnesVideoProvider, VideoRequest


# ── .env 自动加载 ─────────────────────────────────────
def load_env_file() -> None:
    """优先从仓库根目录加载 .env，再尝试当前目录。"""
    repo_env = Path(__file__).resolve().parents[2] / ".env"
    cwd_env = Path.cwd() / ".env"
    candidates = [repo_env, cwd_env]
    seen: set[Path] = set()
    for env_path in candidates:
        env_path = env_path.resolve()
        if env_path in seen or not env_path.exists():
            continue
        seen.add(env_path)
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_env_file()


def env_or_default(name: str, default: str) -> str:
    """读取环境变量；变量不存在或为空字符串时使用默认值。"""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value


# ── 基础配置 ─────────────────────────────────────
MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY", "").strip()
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "").strip()
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "").strip()

# 可选付费图片渠道
OPENAI_IMAGE_API_KEY = os.getenv("OPENAI_IMAGE_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip()
OPENAI_IMAGE_BASE_URL = os.getenv("OPENAI_IMAGE_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2"

# Agnes AI 视频生成
AGNES_API_KEY = os.getenv("AGNES_API_KEY", "").strip()
AGNES_BASE_URL = os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")

PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "9890"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://localhost:{PROXY_PORT}").rstrip("/")

STATE_DIR = Path(env_or_default("IMAGE_PROXY_STATE_DIR", os.path.expanduser("~/.image-proxy")))
QUOTA_FILE = Path(env_or_default("QUOTA_FILE", str(STATE_DIR / "quota_state.json")))
OUTPUT_DIR = Path(env_or_default("OUTPUT_DIR", str(STATE_DIR / "generated")))

MODELSCOPE_DAILY_LIMIT = int(os.getenv("MODELSCOPE_DAILY_LIMIT", "50"))

MODELSCOPE_SUBMIT_TASK_TYPE = os.getenv("MODELSCOPE_SUBMIT_TASK_TYPE", "text-to-image-generation")
MODELSCOPE_POLL_TASK_TYPE = os.getenv("MODELSCOPE_POLL_TASK_TYPE", "image_generation")

MAX_POLL_TIME = int(os.getenv("MAX_POLL_TIME", "120"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "3"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "60"))

# 硅基流动 Kolors 通道支持固定尺寸集合
KOLORS_SIZES = {
    "1024x1024",
    "960x1280",
    "768x1024",
    "720x1440",
    "720x1280",
}

MODELSCOPE_MODELS = [
    "Qwen/Qwen-Image-2512",
    "black-forest-labs/FLUX.1-Krea-dev",
    "Tongyi-MAI/Z-Image",
    "Tongyi-MAI/Z-Image-Turbo",
]

POLLINATIONS_DEFAULT_MODEL = os.getenv("POLLINATIONS_MODEL", "zimage")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("image-proxy")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Image Proxy Gateway", version="v0.2")
app.mount("/generated", StaticFiles(directory=str(OUTPUT_DIR)), name="generated")

# Agnes 图片高级能力允许透传的字段白名单。
# 不直接透传任意 extra 字段，避免用户把无关字段注入到后端请求体。
AGNES_IMAGE_EXTRA_ALLOWLIST = {
    "image",
    "image_url",
    "input_image",
    "input_images",
    "images",
    "mask",
    "mask_url",
    "strength",
    "guidance_scale",
    "num_inference_steps",
    "extra_body",
    "tags",
}


# ── 异常类型 ────────────────────────────────────────────
class RateLimited(RuntimeError):
    pass


class BackendUnavailable(RuntimeError):
    pass


# ── 本地额度计数持久化 ─────────────────────────────────
class QuotaState:
    """魔搭图片额度的本地保护计数器。"""

    def __init__(self) -> None:
        self.today = date.today()
        self.remaining = MODELSCOPE_DAILY_LIMIT
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        try:
            if QUOTA_FILE.exists():
                data = json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
                if date.fromisoformat(data.get("date", "")) == self.today:
                    self.remaining = int(data.get("remaining", MODELSCOPE_DAILY_LIMIT))
                    log.info("已加载魔搭本地保护额度： %s/%s", self.remaining, MODELSCOPE_DAILY_LIMIT)
        except Exception as exc:
            log.warning("忽略损坏的额度状态文件： %s", exc)

    def _save(self) -> None:
        QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        QUOTA_FILE.write_text(
            json.dumps({"date": self.today.isoformat(), "remaining": self.remaining}, indent=2),
            encoding="utf-8",
        )

    def _reset_if_needed(self) -> None:
        current = date.today()
        if current != self.today:
            self.today = current
            self.remaining = MODELSCOPE_DAILY_LIMIT
            self._save()
            log.info("魔搭本地保护额度已重置： %s/%s", self.remaining, MODELSCOPE_DAILY_LIMIT)

    async def available(self) -> bool:
        async with self._lock:
            self._reset_if_needed()
            return self.remaining > 0

    async def consume_one(self) -> None:
        async with self._lock:
            self._reset_if_needed()
            if self.remaining > 0:
                self.remaining -= 1
                self._save()

    async def mark_exhausted(self) -> None:
        async with self._lock:
            self._reset_if_needed()
            self.remaining = 0
            self._save()


quota = QuotaState()


# ── 请求结构与鉴权 ────────────────────────────────────
class ImageRequest(BaseModel):
    """统一图片请求结构。

    - 对标准 OpenAI 兼容字段做显式声明。
    - 允许额外字段透传，便于 Agnes 这类支持图生图、多图编辑、局部重绘的后端能力。
    """

    model_config = ConfigDict(extra="allow")

    prompt: str = Field(..., min_length=1, max_length=32000)
    model: Optional[str] = None
    n: int = Field(1, ge=1, le=1, description="This gateway currently returns one image per request.")
    size: str = Field("1024x1024", description="WIDTHxHEIGHT, for example 1024x1024")
    response_format: Literal["url", "b64_json"] = "url"
    quality: Optional[str] = None
    user: Optional[str] = None
    safe: Optional[Any] = None
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None


async def require_auth(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> None:
    if not GATEWAY_API_KEY:
        return
    if authorization == f"Bearer {GATEWAY_API_KEY}" or x_api_key == GATEWAY_API_KEY:
        return
    raise HTTPException(status_code=401, detail="缺少或无效的网关访问密钥")


def parse_size(size: str) -> tuple[int, int]:
    try:
        width_text, height_text = size.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
        if not (64 <= width <= 4096 and 64 <= height <= 4096):
            raise ValueError
        return width, height
    except Exception as exc:
        raise HTTPException(status_code=400, detail="size 必须是 1024x1024 这类格式") from exc


def extract_extra_image_options(req: ImageRequest) -> dict[str, Any]:
    """提取 Agnes 图片能力允许透传的扩展字段。

    当前主要用于 Agnes 图片模型，以支持图生图、多图参考、mask 重绘等能力。
    只透传白名单字段，既保留扩展能力，也避免把任意无关字段注入后端。
    """
    payload = req.model_dump(exclude_none=True)
    payload.pop("model", None)
    return {key: value for key, value in payload.items() if key in AGNES_IMAGE_EXTRA_ALLOWLIST}


def has_agnes_image_input(payload: dict[str, Any]) -> bool:
    """判断 Agnes 请求是否包含图生图/图片编辑输入。"""
    if any(payload.get(key) for key in ("image", "image_url", "input_image", "input_images", "images")):
        return True
    extra_body = payload.get("extra_body")
    if isinstance(extra_body, dict) and any(extra_body.get(key) for key in ("image", "images", "input_image", "input_images")):
        return True
    return False


def openai_image_response(url: Optional[str] = None, b64_json: Optional[str] = None) -> dict[str, Any]:
    item: dict[str, str] = {}
    if b64_json is not None:
        item["b64_json"] = b64_json
    elif url is not None:
        item["url"] = url
    else:
        raise RuntimeError("图片响应必须包含 url 或 b64_json")
    return {"created": int(time.time()), "data": [item]}


async def maybe_to_b64(result: dict[str, Any], response_format: str) -> dict[str, Any]:
    if response_format == "url":
        return result
    item = result.get("data", [{}])[0]
    if "b64_json" in item:
        return result
    url = item.get("url")
    if not url:
        raise RuntimeError("后端没有返回 url 或 b64_json")
    async with httpx.AsyncClient(follow_redirects=True, timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return openai_image_response(b64_json=base64.b64encode(resp.content).decode("ascii"))


@dataclass(frozen=True)
class RouteTarget:
    provider: str
    model: str
    paid: bool = False


class ProviderBase:
    name: str

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        raise NotImplementedError

    def health(self) -> Any:
        return "unknown"


class SiliconFlowProvider(ProviderBase):
    name = "siliconflow"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not SILICONFLOW_API_KEY:
            raise BackendUnavailable("SILICONFLOW_API_KEY is not configured")

        image_size = req.size if req.size in KOLORS_SIZES else "1024x1024"
        payload = {
            "model": target.model,
            "prompt": req.prompt,
            "image_size": image_size,
            "batch_size": 1,
            "num_inference_steps": 20,
            "guidance_scale": 7.5,
        }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://api.siliconflow.cn/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
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
        return "configured" if SILICONFLOW_API_KEY else "not_configured"


class ModelScopeProvider(ProviderBase):
    name = "modelscope"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not MODELSCOPE_API_KEY:
            raise BackendUnavailable("MODELSCOPE_API_KEY is not configured")
        if not await quota.available():
            raise RateLimited("local ModelScope quota is exhausted")

        base_url = "https://api-inference.modelscope.cn"
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            submit = await client.post(
                f"{base_url}/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {MODELSCOPE_API_KEY}",
                    "Content-Type": "application/json",
                    "X-ModelScope-Async-Mode": "true",
                    "X-ModelScope-Task-Type": MODELSCOPE_SUBMIT_TASK_TYPE,
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
                raise BackendUnavailable(
                    f"ModelScope returned non-JSON submit response: {submit.text[:300]}"
                ) from exc

            task_id = data.get("task_id")
            if not task_id:
                raise BackendUnavailable(f"ModelScope submit response has no task_id: {data}")

            await quota.consume_one()
            log.info("ModelScope task submitted: model=%s task_id=%s remaining=%s", target.model, task_id, quota.remaining)

            deadline = time.time() + MAX_POLL_TIME
            while time.time() < deadline:
                await asyncio.sleep(POLL_INTERVAL)
                poll = await client.get(
                    f"{base_url}/v1/tasks/{task_id}",
                    headers={
                        "Authorization": f"Bearer {MODELSCOPE_API_KEY}",
                        "X-ModelScope-Task-Type": MODELSCOPE_POLL_TASK_TYPE,
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

        raise BackendUnavailable(f"ModelScope polling timed out after {MAX_POLL_TIME}s")

    def health(self) -> dict[str, Any]:
        return {
            "configured": bool(MODELSCOPE_API_KEY),
            "remaining_local_counter": quota.remaining,
            "daily_limit_local_counter": MODELSCOPE_DAILY_LIMIT,
        }


class PollinationsProvider(ProviderBase):
    name = "pollinations"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        width, height = parse_size(req.size)

        if POLLINATIONS_API_KEY:
            payload: dict[str, Any] = {
                "prompt": req.prompt,
                "model": target.model or POLLINATIONS_DEFAULT_MODEL,
                "n": 1,
                "size": f"{width}x{height}",
                "response_format": req.response_format,
            }
            if req.safe is not None:
                payload["safe"] = req.safe
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.post(
                    "https://gen.pollinations.ai/v1/images/generations",
                    headers={
                        "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
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
            "model": target.model or POLLINATIONS_DEFAULT_MODEL,
            "nologo": "true",
        }
        if req.safe is not None:
            query["safe"] = str(req.safe).lower()
        legacy_url = f"https://image.pollinations.ai/prompt/{encoded}?{urllib.parse.urlencode(query)}"

        async with httpx.AsyncClient(follow_redirects=True, timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(legacy_url)
        content_type = resp.headers.get("content-type", "")
        if resp.status_code != 200 or not content_type.startswith("image/"):
            raise BackendUnavailable(f"Pollinations legacy endpoint failed: {resp.status_code} {content_type}")

        ext = "png" if "png" in content_type else "jpg"
        filename = f"pollinations_{int(time.time() * 1000)}.{ext}"
        path = OUTPUT_DIR / filename
        path.write_bytes(resp.content)
        if req.response_format == "b64_json":
            return openai_image_response(b64_json=base64.b64encode(resp.content).decode("ascii"))
        return openai_image_response(url=f"{PUBLIC_BASE_URL}/generated/{filename}")

    def health(self) -> str:
        return "configured_key" if POLLINATIONS_API_KEY else "legacy_public_endpoint"


class OpenAICompatibleImageProvider(ProviderBase):
    name = "openai_image"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not OPENAI_IMAGE_API_KEY:
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

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{OPENAI_IMAGE_BASE_URL}/images/generations",
                headers={
                    "Authorization": f"Bearer {OPENAI_IMAGE_API_KEY}",
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
        return "configured" if OPENAI_IMAGE_API_KEY else "not_configured"


class AgnesImageProvider(ProviderBase):
    name = "agnes_image"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not AGNES_API_KEY:
            raise BackendUnavailable("AGNES_API_KEY is not configured")

        payload: dict[str, Any] = {
            "model": target.model,
            "prompt": req.prompt,
            "n": req.n,
            "size": req.size,
        }
        # Agnes API 的 response_format 需要放在 extra_body 下面，不支持顶层参数
        extra_body = {"response_format": req.response_format}
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

        # Agnes 图片模型支持的高级字段很多（例如图生图、多图编辑、mask 重绘等），
        # 这里做透传，避免每加一种能力就改一次网关结构。
        payload.update(extract_extra_image_options(req))

        # Agnes 图生图/图片编辑请求需要 img2img 标签；用户忘记写时自动补上。
        if has_agnes_image_input(payload):
            tags = payload.setdefault("tags", [])
            if isinstance(tags, str):
                tags = [tags]
                payload["tags"] = tags
            if isinstance(tags, list) and "img2img" not in tags:
                tags.append("img2img")

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{AGNES_BASE_URL}/images/generations",
                headers={
                    "Authorization": f"Bearer {AGNES_API_KEY}",
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

        data = resp.json()
        return normalize_image_response(data)

    def health(self) -> str:
        return "configured" if AGNES_API_KEY else "not_configured"


def normalize_image_response(data: dict[str, Any]) -> dict[str, Any]:
    """把不同图片接口的返回统一成 OpenAI 图片响应格式。"""
    if isinstance(data.get("data"), list) and data["data"]:
        item = data["data"][0]
        if isinstance(item, dict) and (item.get("url") or item.get("b64_json")):
            return data

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


PROVIDERS: dict[str, ProviderBase] = {
    "siliconflow": SiliconFlowProvider(),
    "modelscope": ModelScopeProvider(),
    "pollinations": PollinationsProvider(),
    "openai_image": OpenAICompatibleImageProvider(),
    "agnes_image": AgnesImageProvider(),
}

MODEL_ALIASES: dict[str, RouteTarget] = {
    "qwen": RouteTarget("modelscope", "Qwen/Qwen-Image-2512"),
    "flux": RouteTarget("modelscope", "black-forest-labs/FLUX.1-Krea-dev"),
    "z-image": RouteTarget("modelscope", "Tongyi-MAI/Z-Image"),
    "z-turbo": RouteTarget("modelscope", "Tongyi-MAI/Z-Image-Turbo"),
    "kolors": RouteTarget("siliconflow", "Kwai-Kolors/Kolors"),
    "siliconflow": RouteTarget("siliconflow", "Kwai-Kolors/Kolors"),
    "pollinations": RouteTarget("pollinations", POLLINATIONS_DEFAULT_MODEL),
    # 可选付费渠道
    "gpt-image-2": RouteTarget("openai_image", OPENAI_IMAGE_MODEL, paid=True),
    "openai-image": RouteTarget("openai_image", OPENAI_IMAGE_MODEL, paid=True),
    "agnes-image": RouteTarget("agnes_image", os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash"), paid=True),
    "agnes-2.1": RouteTarget("agnes_image", "agnes-image-2.1-flash", paid=True),
    "agnes-2.0": RouteTarget("agnes_image", "agnes-image-2.0-flash", paid=True),
}

agnes_video = AgnesVideoProvider(
    api_key=AGNES_API_KEY,
    base_url=AGNES_BASE_URL,
    timeout=HTTP_TIMEOUT,
    max_poll_time=int(os.getenv("AGNES_VIDEO_MAX_POLL_TIME", "600")),
    poll_interval=float(os.getenv("AGNES_VIDEO_POLL_INTERVAL", "5")),
)

DEFAULT_CHAIN: list[RouteTarget] = [
    RouteTarget("siliconflow", "Kwai-Kolors/Kolors"),
    *[RouteTarget("modelscope", model) for model in MODELSCOPE_MODELS],
    RouteTarget("pollinations", POLLINATIONS_DEFAULT_MODEL),
]


def resolve_chain(model: Optional[str]) -> list[RouteTarget]:
    if not model:
        return DEFAULT_CHAIN

    raw = model.strip()
    lowered = raw.lower()
    if lowered in MODEL_ALIASES:
        target = MODEL_ALIASES[lowered]
        if target.provider == "pollinations":
            return [target]
        if target.provider in {"openai_image", "agnes_image"}:
            return [target]
        return [target, RouteTarget("pollinations", POLLINATIONS_DEFAULT_MODEL)]

    if raw == "Kwai-Kolors/Kolors" or raw.startswith("Kwai-"):
        return [RouteTarget("siliconflow", raw), RouteTarget("pollinations", POLLINATIONS_DEFAULT_MODEL)]
    return [RouteTarget("modelscope", raw), RouteTarget("pollinations", POLLINATIONS_DEFAULT_MODEL)]


# ── 接口入口 ─────────────────────────────────────
@app.post("/v1/images/generations", dependencies=[Depends(require_auth)])
async def create_image(req: ImageRequest) -> dict[str, Any]:
    chain = resolve_chain(req.model)
    errors: list[str] = []

    for target in chain:
        backend = target.provider
        model = target.model
        provider = PROVIDERS.get(backend)
        if provider is None:
            errors.append(f"{backend}/{model}: unknown provider")
            continue

        try:
            result = await provider.generate(req, target)
            if backend != "pollinations":
                result = await maybe_to_b64(result, req.response_format)
            log.info("%s succeeded: model=%s", backend, model)
            return result
        except RateLimited as exc:
            message = f"{backend}/{model}: {exc}"
            log.warning(message)
            errors.append(message)
            continue
        except Exception as exc:
            message = f"{backend}/{model}: {exc}"
            log.warning(message)
            errors.append(message)
            continue

    raise HTTPException(status_code=502, detail={"message": "all image backends failed", "errors": errors})


# ── Agnes AI 视频接口入口 ──────────────────────────────
@app.post("/v1/videos", dependencies=[Depends(require_auth)])
async def create_video(req: VideoRequest) -> dict[str, Any]:
    """提交 Agnes AI 视频任务。默认只提交任务；需要同步等待时设置 wait_for_completion=true。"""
    try:
        if req.wait_for_completion:
            result = await agnes_video.generate_video(req)
            log.info("Agnes AI 视频生成完成：task_id=%s", result.get("task_id"))
            return result
        result = await agnes_video.submit_task(req)
        log.info("Agnes AI 视频任务已提交：task_id=%s", result.get("task_id") or result.get("id"))
        return result
    except RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except (BackendUnavailable, AgnesVideoError) as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/v1/videos/{task_id}", dependencies=[Depends(require_auth)])
async def get_video(task_id: str) -> dict[str, Any]:
    """查询 Agnes AI 视频任务状态。"""
    try:
        return await agnes_video.poll_task(task_id)
    except (BackendUnavailable, AgnesVideoError) as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "auth_enabled": bool(GATEWAY_API_KEY),
        "siliconflow": PROVIDERS["siliconflow"].health(),
        "modelscope": PROVIDERS["modelscope"].health(),
        "pollinations": PROVIDERS["pollinations"].health(),
        "openai_image": {
            "configured": bool(OPENAI_IMAGE_API_KEY),
            "base_url": OPENAI_IMAGE_BASE_URL,
            "default_model": OPENAI_IMAGE_MODEL,
        },
        "agnes_image": PROVIDERS["agnes_image"].health(),
        "agnes_video": agnes_video.health(),
        "public_base_url": PUBLIC_BASE_URL,
        "models": list(MODEL_ALIASES.keys()),
    }


@app.get("/v1/models", dependencies=[Depends(require_auth)])
async def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": alias, "object": "model", "owned_by": target.provider}
            for alias, target in MODEL_ALIASES.items()
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT)
