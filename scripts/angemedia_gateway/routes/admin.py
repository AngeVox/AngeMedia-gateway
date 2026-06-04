"""管理后台 API 路由。"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException, Request, Response

from ..config_metadata import metadata_response, validate_config_settings
from ..schemas import ConfigUpdateRequest
from ..security import ensure_public_http_url
from ..services.admin_service import AdminService
from ..state import (
    BUILTIN_PROVIDER_CONFIG_KEYS,
    change_admin_password,
    clear_admin_login_failures,
    create_admin_session,
    delete_admin_session,
    get_admin_login_lock,
    get_admin_session,
    get_custom_provider,
    get_config,
    list_custom_providers,
    record_admin_login_failure,
    update_custom_provider_test,
    verify_admin_login,
)
from ..runtime import client_ip_from_request, gateway_key_matches, now_seconds, require_admin_auth

router = APIRouter()
admin_service = AdminService()


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
    return admin_service.admin_config()


@router.get("/v1/admin/config-metadata", dependencies=[Depends(require_admin_auth)])
async def get_admin_config_metadata() -> dict[str, Any]:
    return metadata_response()


@router.post("/v1/admin/config", dependencies=[Depends(require_admin_auth)])
async def update_admin_config(req: ConfigUpdateRequest) -> dict[str, Any]:
    settings = validate_config_settings(dict(req.settings))
    return admin_service.save_config(settings)


@router.post("/v1/admin/gateway-key", dependencies=[Depends(require_admin_auth)])
async def create_gateway_key(save: bool = Body(True, embed=True)) -> dict[str, Any]:
    """生成 am- 前缀网关密钥。save=true 时自动写入配置并立即生效。"""
    return admin_service.create_gateway_key(save)


@router.get("/v1/admin/providers", dependencies=[Depends(require_admin_auth)])
async def get_custom_providers() -> dict[str, Any]:
    return {"data": list_custom_providers(include_secret=False)}


@router.get("/v1/admin/provider-templates", dependencies=[Depends(require_admin_auth)])
async def get_provider_templates() -> dict[str, Any]:
    return {"data": admin_service.provider_templates()}


@router.post("/v1/admin/providers", dependencies=[Depends(require_admin_auth)])
async def save_custom_provider(provider: dict[str, Any]) -> dict[str, Any]:
    try:
        data = admin_service.save_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": data}


@router.post("/v1/admin/providers/{provider_id}/enabled", dependencies=[Depends(require_admin_auth)])
async def set_provider_enabled(provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    enabled = str(payload.get("enabled", "true")).strip().lower() in {"1", "true", "yes", "on"}
    return {"ok": True, "data": admin_service.set_provider_enabled(provider_id, enabled)}


@router.post("/v1/admin/providers/{provider_id}/sort", dependencies=[Depends(require_admin_auth)])
async def set_provider_sort(provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if provider_id in BUILTIN_PROVIDER_CONFIG_KEYS:
        raise HTTPException(status_code=400, detail="内置渠道排序固定；默认链路顺序由网关维护")
    try:
        sort_order = int(payload.get("sort_order"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="排序值必须是整数") from exc
    return {"ok": True, "data": admin_service.sort_provider(provider_id, sort_order)}


@router.post("/v1/admin/providers/{provider_id}/test", dependencies=[Depends(require_admin_auth)])
async def test_provider(provider_id: str) -> dict[str, Any]:
    if provider_id in BUILTIN_PROVIDER_CONFIG_KEYS:
        item = next((row for row in admin_service.builtin_provider_rows() if row["id"] == provider_id), None)
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
    if not admin_service.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="自定义渠道不存在")
    return {"ok": True}


@router.get("/v1/admin/provider-status", dependencies=[Depends(require_admin_auth)])
async def get_provider_status() -> dict[str, Any]:
    """返回普通用户可读的渠道状态；自定义渠道可选查询 status/quota。"""
    try:
        return await admin_service.provider_status()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
