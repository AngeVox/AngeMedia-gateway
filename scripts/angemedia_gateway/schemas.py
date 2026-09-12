"""Pydantic 请求模型。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .video_models import AGNES_VIDEO_V25_MODEL, is_agnes_video_v25


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
    output_format: Optional[Literal["png", "jpeg"]] = None
    watermark: Optional[bool] = None
    user: Optional[str] = None
    safe: Optional[Any] = None
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None
    steps: Optional[int] = Field(None, ge=1, le=1000)
    guidance: Optional[float] = Field(None, ge=0, le=1000)
    operation: Literal["auto", "generate", "edit"] = "auto"
    image: Optional[str] = None
    reference_images: Optional[list[str]] = Field(None, max_length=10)
    mask: Optional[str] = None
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
    """提示词增强请求。"""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(..., min_length=1, max_length=4000)
    media_type: Literal["auto", "image", "video"] = "image"
    style: Optional[str] = Field(None, max_length=200)
    language: Literal["auto", "zh", "en"] = "auto"
    target_language: Literal["en"] = "en"
    strength: Literal["auto", "light", "standard", "medium", "strong"] = "auto"
    negative_prompt: Optional[str] = Field(None, max_length=1000)

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("prompt 不能为空")
        return normalized


class ConfigUpdateRequest(BaseModel):
    """管理后台配置更新。"""

    settings: dict[str, str] = Field(default_factory=dict)


class AssistantRequest(BaseModel):
    """Ange 小助手请求。"""

    model_config = ConfigDict(extra="forbid")

    message: Optional[str] = Field(None, max_length=4000)
    prompt: Optional[str] = Field(None, max_length=4000)
    media_type: Literal["auto", "image", "video"] = "auto"
    language: Literal["auto", "zh", "en"] = "auto"
    target_prompt_language: Literal["en"] = "en"
    context: Optional[dict[str, Any]] = None
    images: Optional[list[str]] = None
    image_roles: Optional[list[dict[str, str]]] = None
    size: Optional[str] = None
    wait_for_completion: bool = False
    confirm_plan: bool = False

    @model_validator(mode="after")
    def normalize_message(self) -> "AssistantRequest":
        text = (self.message if self.message is not None else self.prompt) or ""
        normalized = str(text).strip()
        if not normalized:
            raise ValueError("message 不能为空")
        self.message = normalized
        self.prompt = normalized
        return self


class VideoRequest(BaseModel):
    """统一视频生成请求，兼容 Agnes Video v2.0 与 2.5。"""

    prompt: str = Field(..., min_length=1, max_length=32000)
    model: str = Field(AGNES_VIDEO_V25_MODEL)
    image: Optional[str] = Field(None, description="兼容单张参考图；2.5 reference 模式会归入 images")
    images: Optional[list[str]] = Field(None, max_length=8, description="多张参考图")
    first_frame: Optional[str] = Field(None, description="Agnes Video 2.5 keyframe 首帧公网 URL")
    last_frame: Optional[str] = Field(None, description="Agnes Video 2.5 keyframe 尾帧公网 URL")
    mode: Optional[str] = Field(None, description="v2.0 keyframes；2.5 text/keyframe/reference")
    seconds: Optional[str] = Field(None, description="Agnes Video 2.5 时长，字符串 4-12")
    size: Optional[str] = Field(None, description="Agnes Video 2.5 分辨率档位")
    aspect_ratio: Optional[str] = Field(None, description="Agnes Video 2.5 输出比例")
    height: int = Field(768, ge=256, le=1536)
    width: int = Field(1152, ge=256, le=2048)
    num_frames: int = Field(121, description="v2.0 允许值：81、121、161、241、441")
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

    @model_validator(mode="after")
    def validate_video_model_contract(self) -> "VideoRequest":
        if not is_agnes_video_v25(self.model):
            return self

        forbidden = {
            "height", "width", "num_frames", "frame_rate", "negative_prompt", "num_inference_steps"
        }.intersection(self.model_fields_set)
        if forbidden:
            raise ValueError(
                "Agnes Video 2.5 不支持参数: " + ", ".join(sorted(forbidden))
            )
        if self.extra_body:
            raise ValueError("Agnes Video 2.5 请使用显式字段，不接受 extra_body")

        raw_seconds = str(self.seconds or "5").strip()
        if not raw_seconds.isdigit() or not (4 <= int(raw_seconds) <= 12):
            raise ValueError("Agnes Video 2.5 seconds 只允许字符串 4-12")
        self.seconds = raw_seconds

        self.size = str(self.size or "720P").strip().upper()
        if self.size not in {"720P", "1080P", "1K", "2K"}:
            raise ValueError("Agnes Video 2.5 size 只允许 720P、1080P、1K、2K")

        self.aspect_ratio = str(self.aspect_ratio or "16:9").strip()
        if self.aspect_ratio not in {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
            raise ValueError("Agnes Video 2.5 aspect_ratio 不受支持")

        if not self.mode:
            if self.first_frame or self.last_frame:
                self.mode = "keyframe"
            elif self.image or self.images:
                self.mode = "reference"
            else:
                self.mode = "text"
        self.mode = str(self.mode).strip().lower()
        if self.mode not in {"text", "keyframe", "reference"}:
            raise ValueError("Agnes Video 2.5 mode 只允许 text、keyframe、reference")

        reference_images = ([self.image] if self.image else []) + list(self.images or [])
        if len(reference_images) > 8:
            raise ValueError("Agnes Video 2.5 最多支持 8 张参考图")
        if self.mode == "text" and (reference_images or self.first_frame or self.last_frame):
            raise ValueError("Agnes Video 2.5 text 模式不允许参考媒体")
        if self.mode == "keyframe":
            if not (self.first_frame or self.last_frame):
                raise ValueError("Agnes Video 2.5 keyframe 模式至少需要 first_frame 或 last_frame")
            if reference_images:
                raise ValueError("Agnes Video 2.5 keyframe 模式不允许 images")
        if self.mode == "reference":
            if not reference_images:
                raise ValueError("Agnes Video 2.5 reference 模式至少需要一张参考图")
            if self.first_frame or self.last_frame:
                raise ValueError("Agnes Video 2.5 reference 模式不允许 first_frame/last_frame")
        return self
