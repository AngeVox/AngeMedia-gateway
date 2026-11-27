"""Admin authentication and account routes."""
from __future__ import annotations

import os
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from ..repositories.admin_auth import (
    change_admin_password,
    change_admin_username,
    clear_admin_login_failures,
    authenticate_admin_session,
    delete_admin_session,
    get_admin_login_lock,
    get_admin_session,
    record_admin_login_failure,
    update_admin_account,
)
from ..runtime import check_admin_session_context, client_ip_from_request, now_seconds, require_admin_auth
from ..security import admin_session_context
from ..services.studio_assets import check_studio_revision


router = APIRouter()


class _AccountUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_username: str | None = None
    new_password: str | None = None
    confirm_new_password: str | None = None


@router.post("/v1/admin/login")
async def admin_login(
    payload: dict[str, str], response: Response, request: Request,
    am_admin_session: Optional[str] = Cookie(None),
    x_studio_revision: Annotated[str | None, Header(alias="X-Studio-Revision")] = None,
) -> dict[str, Any]:
    check_studio_revision(x_studio_revision)
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    client_ip = client_ip_from_request(request)
    locked_until = get_admin_login_lock(username, client_ip)
    if locked_until > 0:
        wait_seconds = max(1, int(locked_until - now_seconds()))
        raise HTTPException(status_code=429, detail=f"登录失败次数过多，请 {wait_seconds} 秒后再试")
    created = authenticate_admin_session(username, password, am_admin_session or "") if username and password else None
    if created is None:
        attempt = record_admin_login_failure(username, client_ip)
        if attempt.locked_until > 0:
            raise HTTPException(status_code=429, detail="登录失败次数过多，请 30 秒后再试")
        raise HTTPException(status_code=401, detail="账号或密码错误")
    clear_admin_login_failures(username, client_ip)
    token, expires_at = created
    response.set_cookie(
        "am_admin_session",
        token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("ADMIN_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"},
        max_age=7 * 24 * 3600,
        path="/",
    )
    return {"ok": True, "username": username, "expires_at": expires_at, "session_context": admin_session_context(token)}


@router.post("/v1/admin/logout")
async def admin_logout(
    am_admin_session: Optional[str] = Cookie(None),
    x_studio_session_context: Annotated[str | None, Header(alias="X-Studio-Session-Context")] = None,
    x_studio_revision: Annotated[str | None, Header(alias="X-Studio-Revision")] = None,
) -> dict[str, Any]:
    if am_admin_session:
        check_admin_session_context(am_admin_session, x_studio_session_context)
        check_studio_revision(x_studio_revision)
        delete_admin_session(am_admin_session)
    # 仅撤销本次请求携带的令牌；迟到响应不得删除随后登录写入的新Cookie。
    return {"ok": True}


@router.get("/v1/admin/me")
async def admin_me(session: dict[str, Any] = Depends(require_admin_auth)) -> dict[str, Any]:
    return {"authenticated": True, "username": session["username"], "auth_type": session["auth_type"]}


@router.get("/v1/admin/session")
async def admin_session_status(
    am_admin_session: Optional[str] = Cookie(None),
    x_studio_revision: Annotated[str | None, Header(alias="X-Studio-Revision")] = None,
) -> dict[str, Any]:
    """返回登录状态，不用 401 响应打扰前端控制台。"""
    check_studio_revision(x_studio_revision)
    session = get_admin_session(am_admin_session or "")
    if session is None:
        return {"authenticated": False}
    return {"authenticated": True, "username": session["username"], "auth_type": "session", "session_context": admin_session_context(am_admin_session or "")}


@router.get("/v1/admin/account")
async def admin_account(session: dict[str, Any] = Depends(require_admin_auth)) -> dict[str, Any]:
    if session["auth_type"] != "session":
        raise HTTPException(status_code=403, detail="网关访问密钥不能访问管理账号")
    return {"username": session["username"]}


@router.patch("/v1/admin/account")
async def admin_update_account(
    payload: _AccountUpdateRequest,
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session["auth_type"] != "session":
        raise HTTPException(status_code=403, detail="网关访问密钥不能访问管理账号")
    if payload.new_username is None and payload.new_password is None:
        raise HTTPException(status_code=400, detail="至少需要提供新用户名或新密码")
    if payload.confirm_new_password is not None:
        if payload.new_password is None:
            raise HTTPException(status_code=400, detail="确认密码需要同时提供新密码")
        if payload.new_password != payload.confirm_new_password:
            raise HTTPException(status_code=400, detail="两次输入的新密码不一致")
    updated = update_admin_account(
        session["username"],
        current_password=payload.current_password,
        new_username=payload.new_username,
        new_password=payload.new_password,
    )
    if updated is None:
        # 保持401合同，以明确错误码区分当前密码校验与登录会话失效。
        raise HTTPException(status_code=401, detail={"message": "当前密码错误", "code": "invalid_current_password"})
    # 更新事务已撤销旧会话。避免迟到的删除Cookie响应覆盖更新后重新登录的新Cookie。
    return {"ok": True, "username": updated["username"], "requires_relogin": True}


@router.post("/v1/admin/password")
async def admin_change_password(
    payload: dict[str, str],
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session["auth_type"] != "session":
        raise HTTPException(status_code=400, detail="使用网关密钥鉴权时不能修改管理密码")
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    if not change_admin_password(session["username"], current_password, new_password):
        raise HTTPException(status_code=401, detail="当前密码错误")
    # 仓储已撤销旧会话；与合并账户入口一样，不发送可能迟到的删除Cookie。
    return {"ok": True}


@router.post("/v1/admin/username")
async def admin_change_username(
    payload: dict[str, str],
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session["auth_type"] != "session":
        raise HTTPException(status_code=403, detail="网关访问密钥不能访问管理账号")
    current_password = str(payload.get("current_password") or "")
    new_username = str(payload.get("new_username") or "")
    if not change_admin_username(session["username"], current_password, new_username):
        raise HTTPException(status_code=401, detail="当前密码错误")
    return {"ok": True, "username": new_username.strip()}
