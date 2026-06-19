"""Pydantic 请求模型。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImageRequest(BaseModel):
    """统一图片请求结构。"""

    model_config = ConfigDict(extra="allow")

    prompt: str = Field(..., min_length=1, max_length=32000)
    model: Optional[str] = None
    n: int = Field(1, ge=1, le=1, description="This gateway currently returns one image per request.")
    size: str = Field("1024x1024", description="WIDTHxHEIGHT, for example 1024x1024")
    aspect_ratio: Optional[str] = Field(None, description="Catalog-approved WIDTH:HEIGHT ratio")
    response_format: Literal["url", "b64_json"] = "url"
    quality: Optional[str] = None
    user: Optional[str] = None
    safe: Optional[Any] = None
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None
    steps: Optional[int] = Field(None, ge=1, le=1000)
    guidance: Optional[float] = Field(None, ge=0, le=1000)
    image: Optional[str] = None
    provider_model: Optional[str] = None

    @field_validator("provider_model", mode="before")
    @classmethod
    def normalize_provider_model(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class RouteRequest(BaseModel):
    """轻量路由请求。"""

    prompt: str = Field(..., min_length=1, max_length=32000)
    media_type: Literal["auto", "image", "video"] = "auto"
    images: Optional[list[str]] = None
    requested_model: Optional[str] = None
    size: Optional[str] = None


class EnhanceRequest(BaseModel):
    """轻量提示词增强请求。"""

    prompt: str = Field(..., min_length=1, max_length=32000)
    media_type: Literal["auto", "image", "video"] = "auto"
    style: Optional[str] = None
    strength: Literal["auto", "light", "medium", "strong"] = "auto"
    negative_prompt: Optional[str] = None


class ConfigUpdateRequest(BaseModel):
    """管理后台配置更新。"""

    settings: dict[str, str] = Field(default_factory=dict)


class AssistantRequest(BaseModel):
    """Ange 小助手请求。"""

    prompt: str = Field(..., min_length=1, max_length=32000)
    media_type: Literal["auto", "image", "video"] = "auto"
    images: Optional[list[str]] = None
    image_roles: Optional[list[dict[str, str]]] = None
    size: Optional[str] = None
    wait_for_completion: bool = False
    confirm_plan: bool = False


class VideoRequest(BaseModel):
    """统一视频生成请求。"""

    prompt: str = Field(..., min_length=1, max_length=32000)
    model: str = Field("agnes-video-v2.0")
    image: Optional[str] = Field(None, description="单张输入图片 URL，图生视频时使用")
    images: Optional[list[str]] = Field(None, description="多张输入图片 URL，多图或关键帧模式使用")
    mode: Optional[str] = Field(None, description="生成模式，例如 keyframes")
    height: int = Field(768, ge=256, le=1536)
    width: int = Field(1152, ge=256, le=2048)
    num_frames: int = Field(121, description="允许值：81、121、161、241、441")
    frame_rate: float = Field(24, ge=1, le=60)
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None
    num_inference_steps: Optional[int] = None
    extra_body: Optional[dict[str, Any]] = None
    wait_for_completion: bool = Field(False, description="是否在提交后同步等待完成")

    @field_validator("num_frames")
    @classmethod
    def validate_num_frames(cls, value: int) -> int:
        allowed = {81, 121, 161, 241, 441}
        if value not in allowed:
            raise ValueError("num_frames 只允许 81、121、161、241、441")
        return value
