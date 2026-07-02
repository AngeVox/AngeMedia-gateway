"""Admin routes for multi-key Gateway API keys."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from ..repositories.gateway_keys import (
    create_gateway_api_key,
    get_gateway_api_key,
    list_gateway_api_keys,
    revoke_gateway_api_key,
    update_gateway_api_key,
)
from ..runtime import require_admin_auth


router = APIRouter()


class _GatewayKeyUpdateRequest(BaseModel):
    """PATCH /v1/admin/gateway-keys/{key_id} 请求体。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    note: str | None = None
    enabled: bool | None = None


@router.post("/v1/admin/gateway-keys", dependencies=[Depends(require_admin_auth)])
async def create_gateway_key_admin(payload: dict[str, Any]) -> dict[str, Any]:
    """创建 API 模式 API Key。完整密钥仅在本次响应中返回。"""
    name = str(payload.get("name") or "").strip()
    note = payload.get("note")
    if note is not None:
        note = str(note)
    data = create_gateway_api_key(name=name, note=note)
    return {
        "data": data,
        "warning": "完整密钥仅显示一次，请妥善保存。",
    }


@router.get("/v1/admin/gateway-keys", dependencies=[Depends(require_admin_auth)])
async def list_gateway_keys_admin() -> dict[str, Any]:
    """列出所有 API 模式 API Key。"""
    return {"data": list_gateway_api_keys()}


@router.get("/v1/admin/gateway-keys/{key_id}", dependencies=[Depends(require_admin_auth)])
async def get_gateway_key_admin(key_id: str) -> dict[str, Any]:
    """查询单个 API 模式 API Key。"""
    item = get_gateway_api_key(key_id)
    if item is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"data": item}


@router.patch("/v1/admin/gateway-keys/{key_id}", dependencies=[Depends(require_admin_auth)])
async def update_gateway_key_admin(key_id: str, req: _GatewayKeyUpdateRequest) -> dict[str, Any]:
    """更新 API 模式 API Key 的 name / note / enabled。"""
    item = update_gateway_api_key(
        key_id,
        name=req.name,
        note=req.note,
        enabled=req.enabled,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"data": item}


@router.delete("/v1/admin/gateway-keys/{key_id}", dependencies=[Depends(require_admin_auth)])
async def revoke_gateway_key_admin(key_id: str) -> dict[str, Any]:
    """吊销 API 模式 API Key。"""
    item = get_gateway_api_key(key_id)
    if item is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    ok = revoke_gateway_api_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"ok": True}
