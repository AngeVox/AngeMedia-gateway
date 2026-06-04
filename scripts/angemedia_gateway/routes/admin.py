"""管理后台 API 路由。"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException, Request, Response

from .. import config as C
from ..assistant import assistant_allow_agnes, assistant_allow_paid, assistant_enabled
from ..config_metadata import metadata_response, validate_config_settings
from ..schemas import ConfigUpdateRequest
from ..security import ensure_public_http_url, generate_gateway_key
from ..state import (
    BUILTIN_PROVIDER_CONFIG_KEYS,
    builtin_provider_enabled,
    change_admin_password,
    clear_admin_login_failures,
    config_snapshot,
    create_admin_session,
    delete_admin_session,
    delete_custom_provider,
    get_admin_login_lock,
    get_admin_session,
    get_custom_provider,
    get_config,
    list_custom_providers,
    record_admin_login_failure,
    set_builtin_provider_enabled,
    set_config_many,
    update_custom_provider_enabled,
    update_custom_provider_sort,
    update_custom_provider_test,
    upsert_custom_provider,
    verify_admin_login,
)
from ..runtime import client_ip_from_request, gateway_key_matches, now_seconds, refresh_runtime, require_admin_auth

router = APIRouter()

BUILTIN_PROVIDER_META: list[dict[str, Any]] = [
    {
        "id": "siliconflow",
        "name": "SiliconFlow",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["kolors"],
        "default_model": "Kwai-Kolors/Kolors",
        "sort_order": 10,
        "description": "默认链路首选，适合通用文生图。",
    },
    {
        "id": "modelscope",
        "name": "ModelScope",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["qwen", "flux", "z-image", "z-turbo"],
        "default_model": "Qwen/Qwen-Image-2512",
        "sort_order": 20,
        "description": "承载 Qwen、FLUX、Z-Image 等默认图片模型。",
    },
    {
        "id": "pollinations",
        "name": "Pollinations",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["pollinations"],
        "default_model": C.POLLINATIONS_DEFAULT_MODEL,
        "sort_order": 90,
        "description": "公共兜底渠道，可关闭以避免不可控兜底请求。",
    },
    {
        "id": "agnes_image",
        "name": "Agnes Image",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["agnes-image", "agnes-2.1", "agnes-2.0"],
        "default_model": C.AGNES_IMAGE_MODEL,
        "sort_order": 40,
        "description": "Agnes 图片模型，需要 Agnes 密钥。",
    },
    {
        "id": "openai_image",
        "name": "OpenAI-compatible Image",
        "provider_type": "built_in_image",
        "category": "图片",
        "aliases": ["openai-image", "gpt-image-2"],
        "default_model": C.OPENAI_IMAGE_MODEL,
        "sort_order": 50,
        "description": "显式 OpenAI-compatible 图片渠道，不进入免费默认链路。",
    },
    {
        "id": "agnes_video",
        "name": "Agnes Video",
        "provider_type": "built_in_video",
        "category": "视频",
        "aliases": ["agnes-video-v2.0"],
        "default_model": "agnes-video-v2.0",
        "sort_order": 60,
        "description": "视频任务提交和轮询渠道。",
    },
]

PROVIDER_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "openai-images",
        "name": "OpenAI Images 兼容",
        "description": "标准 /v1/images/generations 接口，适合 OpenAI、转发站或兼容代理。",
        "provider_type": "openai_image",
        "payload": {
            "name": "OpenAI Images",
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-image-2",
            "sort_order": 100,
        },
    },
    {
        "id": "new-api-images",
        "name": "New-API 图片渠道",
        "description": "New-API 中已接入图片模型时可用；按你的部署替换根地址和模型名。",
        "provider_type": "openai_image",
        "payload": {
            "name": "New-API Images",
            "base_url": "https://your-new-api.example.com/v1",
            "default_model": "gpt-image-2",
            "sort_order": 110,
        },
    },
    {
        "id": "custom-images",
        "name": "自定义图片服务",
        "description": "任何返回 data[0].url 或 data[0].b64_json 的 OpenAI Images 兼容服务。",
        "provider_type": "openai_image",
        "payload": {
            "name": "Custom Images",
            "base_url": "https://example.com/v1",
            "default_model": "your-image-model",
            "sort_order": 120,
        },
    },
]


def builtin_configured(provider_id: str) -> bool:
    if provider_id == "siliconflow":
        return bool(C.SILICONFLOW_API_KEY)
    if provider_id == "modelscope":
        return bool(C.MODELSCOPE_API_KEY)
    if provider_id == "pollinations":
        return True
    if provider_id == "openai_image":
        return bool(C.OPENAI_IMAGE_API_KEY)
    if provider_id in {"agnes_image", "agnes_video"}:
        return bool(C.AGNES_API_KEY)
    return False


def builtin_provider_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for meta in BUILTIN_PROVIDER_META:
        enabled = builtin_provider_enabled(str(meta["id"]))
        configured = builtin_configured(str(meta["id"]))
        default_model = str(meta["default_model"])
        if meta["id"] == "openai_image":
            default_model = C.OPENAI_IMAGE_MODEL
        elif meta["id"] == "agnes_image":
            default_model = C.AGNES_IMAGE_MODEL
        elif meta["id"] == "pollinations":
            default_model = C.POLLINATIONS_DEFAULT_MODEL
        rows.append({
            **meta,
            "type": "built_in",
            "source": "built_in",
            "default_model": default_model,
            "enabled": enabled,
            "configured": configured,
            "ready": bool(enabled and configured),
            "removable": True,
            "last_test_status": "configured" if configured else "missing_config",
            "last_response_ms": 0,
        })
    return rows


def custom_provider_status_rows(include_secret: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for provider in list_custom_providers(include_secret=include_secret):
        api_key = str(provider.pop("api_key", "") or "")
        enabled = bool(provider.get("enabled"))
        configured = bool(provider.get("base_url") and provider.get("default_model"))
        row = {
            **provider,
            "type": provider.get("provider_type", "openai_image"),
            "source": "custom",
            "category": "图片",
            "aliases": [f"custom:{provider['id']}"],
            "ready": bool(enabled and configured),
            "configured": configured,
            "removable": True,
        }
        if include_secret:
            row["_api_key"] = api_key
        rows.append(row)
    return rows


async def fetch_openai_model_ids(base_url: str, api_key: str, timeout: float = 15.0) -> tuple[list[str], int]:
    started = time.perf_counter()
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"模型列表拉取失败：HTTP {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    ids = []
    for item in data.get("data", []):
        model_id = item.get("id") if isinstance(item, dict) else None
        if model_id:
            ids.append(str(model_id))
    return sorted(set(ids)), elapsed_ms


@router.post("/v1/admin/login")
async def admin_login(payload: dict[str, str], response: Response, request: Request) -> dict[str, Any]:
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    client_ip = client_ip_from_request(request)
    locked_until = get_admin_login_lock(username, client_ip)
    if locked_until > 0:
        wait_seconds = max(1, int(locked_until - now_seconds()))
        raise HTTPException(status_code=429, detail=f"登录失败次数过多，请 {wait_seconds} 秒后再试")
    if not username or not password or not verify_admin_login(username, password):
        attempt = record_admin_login_failure(username, client_ip)
        if attempt.locked_until > 0:
            raise HTTPException(status_code=429, detail="登录失败次数过多，请 30 秒后再试")
        raise HTTPException(status_code=401, detail="账号或密码错误")
    clear_admin_login_failures(username, client_ip)
    token, expires_at = create_admin_session(username)
    response.set_cookie(
        "am_admin_session",
        token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("ADMIN_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"},
        max_age=7 * 24 * 3600,
        path="/",
    )
    return {"ok": True, "username": username, "expires_at": expires_at}


@router.post("/v1/admin/logout")
async def admin_logout(response: Response, am_admin_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    if am_admin_session:
        delete_admin_session(am_admin_session)
    response.delete_cookie("am_admin_session", path="/")
    return {"ok": True}


@router.get("/v1/admin/me")
async def admin_me(session: dict[str, Any] = Depends(require_admin_auth)) -> dict[str, Any]:
    return {"authenticated": True, "username": session["username"], "auth_type": session["auth_type"]}


@router.get("/v1/admin/session")
async def admin_session_status(
    am_admin_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict[str, Any]:
    """返回登录状态，不用 401 响应打扰前端控制台。"""
    if gateway_key_matches(authorization, x_api_key):
        return {"authenticated": True, "username": "gateway-key", "auth_type": "gateway_key"}
    session = get_admin_session(am_admin_session or "")
    if session is None:
        return {"authenticated": False}
    return {"authenticated": True, "username": session["username"], "auth_type": "session"}


@router.post("/v1/admin/password")
async def admin_change_password(
    payload: dict[str, str],
    response: Response,
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session["auth_type"] != "session":
        raise HTTPException(status_code=400, detail="使用网关密钥鉴权时不能修改管理密码")
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    if not change_admin_password(session["username"], current_password, new_password):
        raise HTTPException(status_code=401, detail="当前密码错误")
    response.delete_cookie("am_admin_session", path="/")
    return {"ok": True}


@router.get("/v1/admin/config", dependencies=[Depends(require_admin_auth)])
async def get_admin_config() -> dict[str, Any]:
    return {
        "settings": config_snapshot(mask=True),
        "db_file": str(C.DB_FILE),
        "upload_dir": str(C.UPLOAD_DIR),
        "output_dir": str(C.OUTPUT_DIR),
        "assistant": {
            "enabled": assistant_enabled(),
            "allow_paid": assistant_allow_paid(),
            "allow_agnes": assistant_allow_agnes(),
            "llm_model": get_config("ANGE_LLM_MODEL", os.getenv("ANGE_LLM_MODEL", "gpt-4o-mini")),
            "llm_base_url": get_config("ANGE_LLM_BASE_URL", os.getenv("ANGE_LLM_BASE_URL", "https://api.openai.com/v1")),
            "configured": bool(get_config("ANGE_LLM_API_KEY", os.getenv("ANGE_LLM_API_KEY", "")).strip()),
        },
        "custom_providers": list_custom_providers(include_secret=False),
        "provider_templates": PROVIDER_TEMPLATES,
    }


@router.get("/v1/admin/config-metadata", dependencies=[Depends(require_admin_auth)])
async def get_admin_config_metadata() -> dict[str, Any]:
    return metadata_response()


@router.post("/v1/admin/config", dependencies=[Depends(require_admin_auth)])
async def update_admin_config(req: ConfigUpdateRequest) -> dict[str, Any]:
    settings = validate_config_settings(dict(req.settings))
    set_config_many(settings)
    refresh_runtime()
    return await get_admin_config()


@router.post("/v1/admin/gateway-key", dependencies=[Depends(require_admin_auth)])
async def create_gateway_key(save: bool = Body(True, embed=True)) -> dict[str, Any]:
    """生成 am- 前缀网关密钥。save=true 时自动写入配置并立即生效。"""
    key = generate_gateway_key()
    if save:
        set_config_many({"GATEWAY_API_KEY": key})
        refresh_runtime()
        return {"saved": True, "key_preview": key[:7] + "****" + key[-4:]}
    return {"key": key, "saved": False}


@router.get("/v1/admin/providers", dependencies=[Depends(require_admin_auth)])
async def get_custom_providers() -> dict[str, Any]:
    return {"data": list_custom_providers(include_secret=False)}


@router.get("/v1/admin/provider-templates", dependencies=[Depends(require_admin_auth)])
async def get_provider_templates() -> dict[str, Any]:
    return {"data": PROVIDER_TEMPLATES}


@router.post("/v1/admin/providers", dependencies=[Depends(require_admin_auth)])
async def save_custom_provider(provider: dict[str, Any]) -> dict[str, Any]:
    try:
        if provider.get("base_url"):
            provider["base_url"] = ensure_public_http_url(str(provider["base_url"]))
        for key in ("status_url", "quota_url"):
            if provider.get(key):
                provider[key] = ensure_public_http_url(str(provider[key]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": upsert_custom_provider(provider)}


@router.post("/v1/admin/providers/{provider_id}/enabled", dependencies=[Depends(require_admin_auth)])
async def set_provider_enabled(provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    enabled = str(payload.get("enabled", "true")).strip().lower() in {"1", "true", "yes", "on"}
    if provider_id in BUILTIN_PROVIDER_CONFIG_KEYS:
        set_builtin_provider_enabled(provider_id, enabled)
        refresh_runtime()
        item = next((row for row in builtin_provider_rows() if row["id"] == provider_id), None)
        return {"ok": True, "data": item}
    return {"ok": True, "data": update_custom_provider_enabled(provider_id, enabled)}


@router.post("/v1/admin/providers/{provider_id}/sort", dependencies=[Depends(require_admin_auth)])
async def set_provider_sort(provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if provider_id in BUILTIN_PROVIDER_CONFIG_KEYS:
        raise HTTPException(status_code=400, detail="内置渠道排序固定；默认链路顺序由网关维护")
    try:
        sort_order = int(payload.get("sort_order"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="排序值必须是整数") from exc
    return {"ok": True, "data": update_custom_provider_sort(provider_id, sort_order)}


@router.post("/v1/admin/providers/{provider_id}/test", dependencies=[Depends(require_admin_auth)])
async def test_provider(provider_id: str) -> dict[str, Any]:
    if provider_id in BUILTIN_PROVIDER_CONFIG_KEYS:
        item = next((row for row in builtin_provider_rows() if row["id"] == provider_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="内置渠道不存在")
        return {
            "ok": bool(item["ready"]),
            "data": item,
            "message": "渠道已启用且关键配置存在" if item["ready"] else "渠道未启用或缺少关键配置",
        }

    provider = get_custom_provider(provider_id, include_secret=True)
    if provider is None:
        raise HTTPException(status_code=404, detail="自定义渠道不存在")
    try:
        base_url = ensure_public_http_url(str(provider.get("base_url") or ""))
        models, elapsed_ms = await fetch_openai_model_ids(base_url, str(provider.get("api_key") or ""))
        status = "ok" if (not models or provider.get("default_model") in models) else "model_not_listed"
        updated = update_custom_provider_test(provider_id, status, elapsed_ms, "" if status == "ok" else "默认模型不在 /models 返回列表中")
        return {"ok": status == "ok", "data": updated, "models": models, "elapsed_ms": elapsed_ms}
    except HTTPException as exc:
        update_custom_provider_test(provider_id, "failed", 0, str(exc.detail))
        raise
    except Exception as exc:
        updated = update_custom_provider_test(provider_id, "failed", 0, str(exc))
        return {"ok": False, "data": updated, "message": f"连接测试失败：{exc}"}


@router.delete("/v1/admin/providers/{provider_id}", dependencies=[Depends(require_admin_auth)])
async def remove_custom_provider(provider_id: str) -> dict[str, Any]:
    if not delete_custom_provider(provider_id):
        raise HTTPException(status_code=404, detail="自定义渠道不存在")
    return {"ok": True}


@router.get("/v1/admin/provider-status", dependencies=[Depends(require_admin_auth)])
async def get_provider_status() -> dict[str, Any]:
    """返回普通用户可读的渠道状态；自定义渠道可选查询 status/quota。"""
    built_in = builtin_provider_rows()
    custom_status: list[dict[str, Any]] = []
    for provider in custom_provider_status_rows(include_secret=True):
        item = dict(provider)
        for key in ("status_url", "quota_url"):
            url = provider.get(key)
            if not url:
                continue
            try:
                url = ensure_public_http_url(str(url))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            headers = {}
            if provider.get("_api_key"):
                headers["Authorization"] = f"Bearer {provider['_api_key']}"
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(url, headers=headers)
                item[key.replace("_url", "")] = {
                    "ok": resp.status_code < 400,
                    "status_code": resp.status_code,
                    "body": resp.text[:500],
                }
            except Exception as exc:
                item[key.replace("_url", "")] = {"ok": False, "error": str(exc)}
        item.pop("_api_key", None)
        custom_status.append(item)
    return {"built_in": built_in, "custom": custom_status, "data": [*built_in, *custom_status]}


@router.get("/v1/admin/assistant/models", dependencies=[Depends(require_admin_auth)])
async def list_assistant_models() -> dict[str, Any]:
    api_key = get_config("ANGE_LLM_API_KEY", os.getenv("ANGE_LLM_API_KEY", "")).strip()
    base_url = get_config("ANGE_LLM_BASE_URL", os.getenv("ANGE_LLM_BASE_URL", "https://api.openai.com/v1")).strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="请先配置 LLM 接口地址")
    try:
        models, elapsed_ms = await fetch_openai_model_ids(base_url, api_key)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"模型列表拉取失败：{exc}") from exc
    return {"data": models, "elapsed_ms": elapsed_ms, "base_url": base_url}


@router.post("/v1/admin/assistant/test", dependencies=[Depends(require_admin_auth)])
async def test_assistant_connection(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    api_key = get_config("ANGE_LLM_API_KEY", os.getenv("ANGE_LLM_API_KEY", "")).strip()
    base_url = get_config("ANGE_LLM_BASE_URL", os.getenv("ANGE_LLM_BASE_URL", "https://api.openai.com/v1")).strip().rstrip("/")
    model = str(payload.get("model") or get_config("ANGE_LLM_MODEL", os.getenv("ANGE_LLM_MODEL", "gpt-4o-mini"))).strip()
    if not base_url or not model:
        raise HTTPException(status_code=400, detail="请先配置 LLM 接口地址和模型")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "temperature": 0.1,
                    "max_tokens": 48,
                    "messages": [
                        {"role": "system", "content": "你是 AngeMedia 连通性测试助手。"},
                        {"role": "user", "content": "请用中文用一句话回复：AngeMedia 小助手连接正常。"},
                    ],
                },
            )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"LLM 测试失败：HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        content = str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        return {"ok": True, "model": model, "elapsed_ms": elapsed_ms, "preview": content[:200]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM 测试失败：{exc}") from exc
