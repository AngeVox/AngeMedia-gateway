"""媒体本地化与响应归一化。"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
import urllib.parse
from pathlib import Path
from typing import Any, Optional

import httpx

from . import config as C
from .security import validate_public_http_url

REMOTE_MEDIA_CHUNK_SIZE = 1024 * 1024
REMOTE_MEDIA_MAX_REDIRECTS = 5


def openai_image_response(*, url: str | None = None, b64_json: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {}
    if url:
        item["url"] = url
    if b64_json:
        item["b64_json"] = b64_json
    return {"created": 0, "data": [item]}


def _is_http_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


async def _send_public_get(client: httpx.AsyncClient, url: str) -> tuple[httpx.Response, str]:
    """打开远端公开 URL，并逐跳校验重定向目标，避免本地化下载 SSRF。"""
    current = validate_public_http_url(url)
    for _ in range(REMOTE_MEDIA_MAX_REDIRECTS + 1):
        request = client.build_request("GET", current)
        response = await client.send(request, stream=True, follow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            await response.aclose()
            if not location:
                raise RuntimeError("远端媒体重定向缺少 Location")
            current = validate_public_http_url(urllib.parse.urljoin(current, location))
            continue
        return response, current
    raise RuntimeError(f"远端媒体重定向超过 {REMOTE_MEDIA_MAX_REDIRECTS} 次")


async def fetch_public_remote_media(url: str) -> tuple[bytes, str, str]:
    """下载公开远端媒体，限制大小，并对初始 URL 与每次重定向做 SSRF 校验。"""
    chunks: list[bytes] = []
    total = 0
    async with httpx.AsyncClient(timeout=C.HTTP_TIMEOUT) as client:
        response, final_url = await _send_public_get(client, url)
        try:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            length_text = response.headers.get("content-length")
            if length_text:
                try:
                    content_length = int(length_text)
                    if content_length > C.MEDIA_DOWNLOAD_MAX_BYTES:
                        raise RuntimeError(f"远端媒体过大：{content_length} bytes，超过 MEDIA_DOWNLOAD_MAX_BYTES")
                except ValueError:
                    pass
            async for chunk in response.aiter_bytes(REMOTE_MEDIA_CHUNK_SIZE):
                if not chunk:
                    continue
                total += len(chunk)
                if total > C.MEDIA_DOWNLOAD_MAX_BYTES:
                    raise RuntimeError(f"远端媒体过大：{total} bytes，超过 MEDIA_DOWNLOAD_MAX_BYTES")
                chunks.append(chunk)
        finally:
            await response.aclose()
    return b"".join(chunks), content_type, final_url


async def maybe_to_b64(result: dict[str, Any], response_format: str) -> dict[str, Any]:
    if response_format == "url":
        return result
    item = result.get("data", [{}])[0]
    if "b64_json" in item:
        return result
    url = item.get("url")
    if not url:
        raise RuntimeError("后端没有返回 url 或 b64_json")
    content, _, _ = await fetch_public_remote_media(str(url))
    return openai_image_response(b64_json=base64.b64encode(content).decode("ascii"))


def is_generated_local_url(url: str) -> bool:
    if not url:
        return False
    if url.startswith(f"{C.PUBLIC_BASE_URL}/generated/"):
        return True
    parsed = urllib.parse.urlparse(url)
    return parsed.path.startswith("/generated/")


def extension_from_response(url: str, content_type: str, fallback_ext: str) -> str:
    content_type = content_type.split(";", 1)[0].strip().lower()
    by_type = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
    }
    if content_type in by_type:
        return by_type[content_type]
    suffix = Path(urllib.parse.urlparse(url).path).suffix
    if suffix and len(suffix) <= 8:
        return suffix
    # S3/CDN 临时链接常把真实图片当 application/octet-stream 返回，
    # 这种通用类型不应覆盖 URL 后缀或调用方提供的兜底扩展名。
    if content_type in {"application/octet-stream", "binary/octet-stream"}:
        return fallback_ext if fallback_ext.startswith(".") else f".{fallback_ext}"
    guessed = mimetypes.guess_extension(content_type) if content_type else None
    if guessed:
        return guessed
    return fallback_ext if fallback_ext.startswith(".") else f".{fallback_ext}"


def stable_filename(prefix: str, url: str, ext: str, stable_id: Optional[str] = None) -> str:
    digest = hashlib.sha256((stable_id or url).encode("utf-8")).hexdigest()[:16]
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", prefix).strip("-") or "media"
    return f"{safe_prefix}_{digest}{ext}"


async def download_remote_media(url: str, prefix: str, fallback_ext: str, stable_id: Optional[str] = None) -> tuple[str, str]:
    if not C.AUTO_DOWNLOAD_GENERATED:
        return url, ""
    if not _is_http_url(url) or is_generated_local_url(url):
        return url, ""

    content, content_type, final_url = await fetch_public_remote_media(url)
    ext = extension_from_response(final_url, content_type, fallback_ext)
    filename = stable_filename(prefix, url, ext, stable_id=stable_id)
    path = C.OUTPUT_DIR / filename
    if not path.exists():
        path.write_bytes(content)
    return f"{C.PUBLIC_BASE_URL}/generated/{filename}", str(path)


async def try_download_remote_media(
    url: str,
    prefix: str,
    fallback_ext: str,
    stable_id: Optional[str] = None,
) -> tuple[str, str, Optional[str]]:
    try:
        local_url, local_path = await download_remote_media(url, prefix, fallback_ext, stable_id=stable_id)
        return local_url, local_path, None
    except Exception as exc:
        if C.LOCALIZE_STRICT:
            raise
        return url, "", str(exc)


async def localize_image_result(result: dict[str, Any], provider_name: str, model_name: str) -> dict[str, Any]:
    data = result.get("data")
    if not C.AUTO_DOWNLOAD_GENERATED:
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data[0]["localized"] = False
        return result
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return result
    item = data[0]
    url = item.get("url")
    if not url:
        return result

    local_url, local_path, error = await try_download_remote_media(
        url,
        prefix=f"image_{provider_name}",
        fallback_ext=".png",
        stable_id=f"{provider_name}:{model_name}:{url}",
    )
    if local_url != url:
        item["remote_url"] = url
        item["url"] = local_url
        item["local_path"] = local_path
        item["localized"] = True
    else:
        item["localized"] = False
    if error:
        item["localize_error"] = error
    return result


async def localize_video_result(result: dict[str, Any]) -> dict[str, Any]:
    if not C.AUTO_DOWNLOAD_GENERATED:
        if result.get("video_url"):
            result["localized"] = False
        return result
    video_url = result.get("video_url")
    if not isinstance(video_url, str) or not video_url:
        return result

    task_id = str(result.get("task_id") or result.get("id") or video_url)
    local_url, local_path, error = await try_download_remote_media(
        video_url,
        prefix="video_agnes",
        fallback_ext=".mp4",
        stable_id=task_id,
    )
    if local_url != video_url:
        result["remote_video_url"] = video_url
        result["video_url"] = local_url
        result["local_path"] = local_path
        result["localized"] = True
    else:
        result["localized"] = False
    if error:
        result["localize_error"] = error
    return result
