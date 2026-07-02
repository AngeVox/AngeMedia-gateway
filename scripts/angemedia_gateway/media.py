"""媒体本地化与响应归一化。"""
from __future__ import annotations

import base64
import errno
import logging
import os
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx

from . import config as C
from .media_filenames import (
    extension_from_response,
    safe_filename_prefix as _safe_filename_prefix,
    stable_filename,
)
from .media_response import openai_image_response
from .media_url_policy import (
    generated_url_local_path,
    is_generated_local_url,
    is_http_url as _is_http_url,
    is_trusted_remote_media_url as _is_trusted_remote_media_url,
)
from .security import validate_public_http_url

REMOTE_MEDIA_CHUNK_SIZE = 1024 * 1024
REMOTE_MEDIA_MAX_REDIRECTS = 5
REMOTE_MEDIA_B64_MAX_BYTES = 25 * 1024 * 1024

log = logging.getLogger("angemedia-gateway")


def _validate_remote_media_url(url: str, trusted_hosts: Iterable[str] | None = None) -> str:
    try:
        return validate_public_http_url(url)
    except ValueError:
        if _is_trusted_remote_media_url(url, trusted_hosts):
            return str(url or "").strip()
        raise


async def _send_public_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    trusted_hosts: Iterable[str] | None = None,
) -> tuple[httpx.Response, str]:
    """打开远端公开 URL，并逐跳校验重定向目标，避免本地化下载 SSRF。"""
    current = _validate_remote_media_url(url, trusted_hosts)
    for _ in range(REMOTE_MEDIA_MAX_REDIRECTS + 1):
        request = client.build_request("GET", current)
        response = await client.send(request, stream=True, follow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            await response.aclose()
            if not location:
                raise RuntimeError("远端媒体重定向缺少 Location")
            current = _validate_remote_media_url(urllib.parse.urljoin(current, location), trusted_hosts)
            continue
        return response, current
    raise RuntimeError(f"远端媒体重定向超过 {REMOTE_MEDIA_MAX_REDIRECTS} 次")


def _remote_media_http_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=C.MEDIA_DOWNLOAD_CONNECT_TIMEOUT,
        read=C.MEDIA_DOWNLOAD_READ_TIMEOUT,
        write=C.MEDIA_DOWNLOAD_WRITE_TIMEOUT,
        pool=C.MEDIA_DOWNLOAD_POOL_TIMEOUT,
    )
    limits = httpx.Limits(
        max_connections=C.MEDIA_DOWNLOAD_CONCURRENCY,
        max_keepalive_connections=C.MEDIA_DOWNLOAD_CONCURRENCY,
    )
    return httpx.AsyncClient(timeout=timeout, limits=limits, trust_env=False)


def cleanup_controlled_download_tmp_dir() -> dict[str, int]:
    """Remove controlled download temp files left by interrupted localize writes."""
    result = {"part_removed": 0, "copying_removed": 0, "errors": 0}
    output_dir = C.OUTPUT_DIR
    tmp_dir = output_dir / ".tmp"
    if tmp_dir.is_symlink():
        log.warning("Download tmp path is a symlink and will not be cleaned: %s", tmp_dir)
        result["errors"] += 1
        return result
    if not tmp_dir.exists():
        return result
    if not tmp_dir.is_dir():
        log.warning("Download tmp path is not a directory: %s", tmp_dir)
        result["errors"] += 1
        return result

    try:
        output_root = output_dir.resolve()
        tmp_root = tmp_dir.resolve()
        tmp_root.relative_to(output_root)
    except (OSError, ValueError) as exc:
        log.warning("Skip unsafe download tmp cleanup for %s: %s", tmp_dir, exc)
        result["errors"] += 1
        return result

    for path in tmp_dir.iterdir():
        if path.suffix not in {".part", ".copying"}:
            continue
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(tmp_root)
        except (OSError, ValueError) as exc:
            log.warning("Skip unsafe download tmp file cleanup for %s: %s", path, exc)
            result["errors"] += 1
            continue
        try:
            path.unlink()
        except OSError as exc:
            log.warning("Failed to remove download tmp file %s: %s", path, exc)
            result["errors"] += 1
            continue
        if path.suffix == ".part":
            result["part_removed"] += 1
        else:
            result["copying_removed"] += 1
    return result


def verify_download_tmp_os_replace_ready() -> None:
    """验证 OUTPUT_DIR/.tmp 与 OUTPUT_DIR 在同一文件系统，os.replace 可正常工作。"""
    tmp_dir = C.OUTPUT_DIR / ".tmp"

    if tmp_dir.is_symlink():
        raise RuntimeError(
            "os.replace self-test 失败：.tmp 是符号链接，不允许使用。"
            "请删除该符号链接并确保 OUTPUT_DIR/.tmp 是普通目录。"
            f"OUTPUT_DIR={C.OUTPUT_DIR}"
        )

    if tmp_dir.exists() and not tmp_dir.is_dir():
        raise RuntimeError(
            "os.replace self-test 失败：.tmp 是普通文件而非目录。"
            "请删除该文件。"
            f"OUTPUT_DIR={C.OUTPUT_DIR}"
        )

    tmp_dir.mkdir(parents=True, exist_ok=True)

    token = uuid.uuid4().hex
    test_tmp = tmp_dir / f"samefs_{token}.tmp"
    test_final = C.OUTPUT_DIR / f"samefs_{token}.test"
    try:
        test_tmp.write_bytes(b"same-filesystem-test")
        try:
            os.replace(str(test_tmp), str(test_final))
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise RuntimeError(
                    "os.replace self-test 失败（EXDEV）：OUTPUT_DIR 和 .tmp 不在同一文件系统，"
                    "os.replace 无法跨设备执行。请检查 Docker volume 挂载或 bind mount 配置，"
                    "确保 OUTPUT_DIR 内部不跨设备。"
                    f"OUTPUT_DIR={C.OUTPUT_DIR}"
                ) from exc
            raise RuntimeError(
                f"os.replace self-test 失败：errno={exc.errno}, {exc}。"
                f"OUTPUT_DIR={C.OUTPUT_DIR}, tmp_dir={tmp_dir}"
            ) from exc
    finally:
        try:
            test_final.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            test_tmp.unlink(missing_ok=True)
        except OSError:
            pass


async def fetch_public_remote_media(
    url: str,
    *,
    trusted_hosts: Iterable[str] | None = None,
    max_bytes: int | None = None,
) -> tuple[bytes, str, str]:
    """下载公开远端媒体，限制大小，并对初始 URL 与每次重定向做 SSRF 校验。"""
    chunks: list[bytes] = []
    total = 0
    limit = int(max_bytes if max_bytes is not None else C.MEDIA_DOWNLOAD_MAX_BYTES)
    async with _remote_media_http_client() as client:
        response, final_url = await _send_public_get(client, url, trusted_hosts=trusted_hosts)
        try:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            length_text = response.headers.get("content-length")
            if length_text:
                try:
                    content_length = int(length_text)
                    if content_length > limit:
                        raise RuntimeError(f"远端媒体过大：{content_length} bytes，超过 {limit} bytes")
                except ValueError:
                    pass
            async for chunk in response.aiter_bytes(REMOTE_MEDIA_CHUNK_SIZE):
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    raise RuntimeError(f"远端媒体过大：{total} bytes，超过 {limit} bytes")
                chunks.append(chunk)
        finally:
            await response.aclose()
    return b"".join(chunks), content_type, final_url


async def _stream_public_remote_media_to_path(
    url: str,
    tmp_path: Path,
    *,
    trusted_hosts: Iterable[str] | None = None,
) -> tuple[str, str]:
    """把公开远端媒体流式写入临时文件，返回 content_type 和 final_url。"""
    total = 0
    async with _remote_media_http_client() as client:
        response, final_url = await _send_public_get(client, url, trusted_hosts=trusted_hosts)
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
            with tmp_path.open("wb") as fh:
                async for chunk in response.aiter_bytes(REMOTE_MEDIA_CHUNK_SIZE):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > C.MEDIA_DOWNLOAD_MAX_BYTES:
                        raise RuntimeError(f"远端媒体过大：{total} bytes，超过 MEDIA_DOWNLOAD_MAX_BYTES")
                    fh.write(chunk)
        finally:
            await response.aclose()
    return content_type, final_url


async def maybe_to_b64(result: dict[str, Any], response_format: str) -> dict[str, Any]:
    if response_format == "url":
        return result
    item = result.get("data", [{}])[0]
    if "b64_json" in item:
        return result
    url = item.get("url")
    if not url:
        raise RuntimeError("后端没有返回 url 或 b64_json")
    content, _, _ = await fetch_public_remote_media(str(url), max_bytes=REMOTE_MEDIA_B64_MAX_BYTES)
    return openai_image_response(b64_json=base64.b64encode(content).decode("ascii"))


async def download_remote_media(
    url: str,
    prefix: str,
    fallback_ext: str,
    stable_id: Optional[str] = None,
    *,
    force: bool = False,
    trusted_hosts: Iterable[str] | None = None,
) -> tuple[str, str]:
    if not force and not C.AUTO_DOWNLOAD_GENERATED:
        return url, ""
    if not _is_http_url(url) or is_generated_local_url(url):
        return url, ""

    tmp_dir = C.OUTPUT_DIR / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"download_{uuid.uuid4().hex}.part"
    try:
        if trusted_hosts:
            content_type, final_url = await _stream_public_remote_media_to_path(
                url,
                tmp_path,
                trusted_hosts=trusted_hosts,
            )
        else:
            content_type, final_url = await _stream_public_remote_media_to_path(url, tmp_path)
        ext = extension_from_response(final_url, content_type, fallback_ext)
        filename = stable_filename(prefix, url, ext, stable_id=stable_id)
        final_path = C.OUTPUT_DIR / filename
        if final_path.exists():
            tmp_path.unlink(missing_ok=True)
            return f"{C.PUBLIC_BASE_URL}/generated/{filename}", str(final_path)
        os.replace(str(tmp_path), str(final_path))
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return f"{C.PUBLIC_BASE_URL}/generated/{filename}", str(final_path)


async def try_download_remote_media(
    url: str,
    prefix: str,
    fallback_ext: str,
    stable_id: Optional[str] = None,
    *,
    force: bool = False,
    trusted_hosts: Iterable[str] | None = None,
) -> tuple[str, str, Optional[str]]:
    try:
        local_url, local_path = await download_remote_media(
            url,
            prefix,
            fallback_ext,
            stable_id=stable_id,
            force=force,
            trusted_hosts=trusted_hosts,
        )
        return local_url, local_path, None
    except Exception as exc:
        if C.LOCALIZE_STRICT:
            raise
        return url, "", str(exc)


async def localize_image_result(
    result: dict[str, Any],
    provider_name: str,
    model_name: str,
    *,
    force: bool = False,
    trusted_hosts: Iterable[str] | None = None,
) -> dict[str, Any]:
    data = result.get("data")
    if not force and not C.AUTO_DOWNLOAD_GENERATED:
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data[0]["localized"] = False
        return result
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return result
    item = data[0]
    url = item.get("url")
    if not url:
        return result
    local_generated_path = generated_url_local_path(str(url))
    if local_generated_path:
        item["local_path"] = local_generated_path
        item["localized"] = True
        return result

    download_kwargs: dict[str, Any] = {"force": force}
    if trusted_hosts is not None:
        download_kwargs["trusted_hosts"] = trusted_hosts
    local_url, local_path, error = await try_download_remote_media(
        url,
        prefix=f"image_{provider_name}",
        fallback_ext=".png",
        stable_id=f"{provider_name}:{model_name}:{url}",
        **download_kwargs,
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


async def localize_video_result(
    result: dict[str, Any],
    *,
    force: bool = False,
    trusted_hosts: Iterable[str] | None = None,
) -> dict[str, Any]:
    if not force and not C.AUTO_DOWNLOAD_GENERATED:
        if result.get("video_url"):
            result["localized"] = False
        return result
    video_url = result.get("video_url")
    if not isinstance(video_url, str) or not video_url:
        return result

    task_id = str(result.get("task_id") or result.get("id") or video_url)
    download_kwargs: dict[str, Any] = {"force": force}
    if trusted_hosts is not None:
        download_kwargs["trusted_hosts"] = trusted_hosts
    local_url, local_path, error = await try_download_remote_media(
        video_url,
        prefix="video_agnes",
        fallback_ext=".mp4",
        stable_id=task_id,
        **download_kwargs,
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
