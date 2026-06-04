"""媒体路由与轻量提示词增强。"""
from __future__ import annotations

from typing import Any, Optional

from . import config as C
from .providers.base import RouteTarget
from .providers.image import parse_size
from .schemas import EnhanceRequest, RouteRequest
from .state import builtin_provider_enabled

MODEL_ALIASES: dict[str, RouteTarget] = {
    "kolors": RouteTarget("siliconflow", "Kwai-Kolors/Kolors"),
    "qwen": RouteTarget("modelscope", "Qwen/Qwen-Image-2512"),
    "qwen-image": RouteTarget("modelscope", "Qwen/Qwen-Image-2512"),
    "flux": RouteTarget("modelscope", "black-forest-labs/FLUX.1-Krea-dev"),
    "flux-krea": RouteTarget("modelscope", "black-forest-labs/FLUX.1-Krea-dev"),
    "z-image": RouteTarget("modelscope", "Tongyi-MAI/Z-Image"),
    "z-turbo": RouteTarget("modelscope", "Tongyi-MAI/Z-Image-Turbo"),
    "z-image-turbo": RouteTarget("modelscope", "Tongyi-MAI/Z-Image-Turbo"),
    "pollinations": RouteTarget("pollinations", C.POLLINATIONS_DEFAULT_MODEL),
    "openai-image": RouteTarget("openai_image", C.OPENAI_IMAGE_MODEL),
    "gpt-image-2": RouteTarget("openai_image", "gpt-image-2"),
    "agnes-image": RouteTarget("agnes_image", C.AGNES_IMAGE_MODEL),
    "agnes-2.1": RouteTarget("agnes_image", "agnes-image-2.1-flash"),
    "agnes-2.0": RouteTarget("agnes_image", "agnes-image-2.0-flash"),
}
DEFAULT_CHAIN = [
    RouteTarget("siliconflow", "Kwai-Kolors/Kolors"),
    RouteTarget("modelscope", "Qwen/Qwen-Image-2512"),
    RouteTarget("modelscope", "black-forest-labs/FLUX.1-Krea-dev"),
    RouteTarget("modelscope", "Tongyi-MAI/Z-Image"),
    RouteTarget("modelscope", "Tongyi-MAI/Z-Image-Turbo"),
    RouteTarget("pollinations", C.POLLINATIONS_DEFAULT_MODEL),
]


def route_target_enabled(target: RouteTarget) -> bool:
    return builtin_provider_enabled(target.provider)

VIDEO_TRIGGERS = ("视频", "动起来", "动画", "运动", "文生视频", "图生视频", "animate", "animation", "video", "motion", "i2v")
TEXT_TRIGGERS = ("标题", "文字", "海报", "标语", "slogan", "headline", "poster", "banner")
PORTRAIT_TRIGGERS = ("写真", "真人", "真实", "照片", "现实风格", "写实", "人像", "肖像", "帅哥", "男性", "男生", "美女", "模特", "photoreal", "portrait", "realistic")
PRODUCT_TRIGGERS = ("产品", "商品", "家居", "室内", "自然光", "风景", "landscape", "product", "interior")
CONCEPT_TRIGGERS = ("超现实", "概念", "梦境", "抽象", "surreal", "concept")
ANIME_TRIGGERS = ("二次元", "动漫", "动画风", "插画", "anime", "manga", "illustration")


def resolve_chain(model: Optional[str]) -> list[RouteTarget]:
    if not model:
        return [target for target in DEFAULT_CHAIN if route_target_enabled(target)]

    raw = model.strip()
    lowered = raw.lower()
    if lowered in MODEL_ALIASES:
        target = MODEL_ALIASES[lowered]
        if not route_target_enabled(target):
            return []
        if target.provider == "pollinations":
            return [target]
        if target.provider in {"openai_image", "agnes_image"}:
            return [target]
        chain = [target, RouteTarget("pollinations", C.POLLINATIONS_DEFAULT_MODEL)]
        return [item for item in chain if route_target_enabled(item)]

    if raw == "Kwai-Kolors/Kolors" or raw.startswith("Kwai-"):
        chain = [RouteTarget("siliconflow", raw), RouteTarget("pollinations", C.POLLINATIONS_DEFAULT_MODEL)]
        return [item for item in chain if route_target_enabled(item)]
    chain = [RouteTarget("modelscope", raw), RouteTarget("pollinations", C.POLLINATIONS_DEFAULT_MODEL)]
    return [item for item in chain if route_target_enabled(item)]


def contains_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def infer_media_type(prompt: str, media_type: str) -> str:
    if media_type != "auto":
        return media_type
    return "video" if contains_any(prompt, VIDEO_TRIGGERS) else "image"


def infer_video_input_mode(prompt: str, images: Optional[list[str]]) -> str:
    count = len(images or [])
    if count >= 2 and contains_any(prompt, ("过渡", "首尾", "从第一张", "到第二张", "keyframe", "transition")):
        return "first_last_frame"
    if count >= 1 and contains_any(prompt, ("参考", "保持风格", "reference")):
        return "reference"
    if count >= 1:
        return "first_frame"
    return "t2v"


def choose_image_model(prompt: str, requested_model: Optional[str] = None) -> Optional[str]:
    if requested_model:
        return requested_model
    if contains_any(prompt, ("agnes", "Agnes")):
        return "agnes-2.1"
    if contains_any(prompt, ("gpt-image", "openai-image")):
        return "gpt-image-2"
    if contains_any(prompt, TEXT_TRIGGERS) or contains_any(prompt, ANIME_TRIGGERS):
        return "qwen"
    if contains_any(prompt, PORTRAIT_TRIGGERS):
        return "z-turbo"
    if contains_any(prompt, PRODUCT_TRIGGERS):
        return "flux"
    if contains_any(prompt, CONCEPT_TRIGGERS):
        return "z-image"
    return None


def choose_default_size(prompt: str, media_type: str) -> str:
    if media_type == "video":
        return "1152x768"
    if contains_any(prompt, ("竖屏", "手机壁纸", "小红书", "portrait", "vertical")):
        return "960x1280"
    if contains_any(prompt, ("横版", "封面", "banner", "landscape", "wide")):
        return "1536x1024"
    return "1024x1024"


def should_enhance_prompt(prompt: str, strength: str) -> bool:
    if strength in {"light", "medium", "strong"}:
        return True
    stripped = prompt.strip()
    if not stripped:
        return False
    detail_groups = (
        ("写实", "真实", "二次元", "动漫", "电影感", "摄影", "插画", "海报", "产品", "风景", "anime", "cinematic"),
        ("构图", "半身", "全身", "特写", "广角", "近景", "远景", "视角", "镜头", "景深"),
        ("光影", "光线", "自然光", "逆光", "柔光", "色彩", "氛围", "背景"),
        ("服装", "表情", "姿态", "动作", "材质", "细节", "皮肤", "发丝"),
        ("不要", "避免", "负面", "水印", "低清晰度", "畸形", "错误文字"),
    )
    detail_score = sum(1 for group in detail_groups if contains_any(stripped, group))
    request_like = contains_any(stripped, ("帮我", "生成", "画", "做一张", "来一张", "make", "create", "draw"))
    if len(stripped) < 36:
        return True
    if len(stripped) < 90 and detail_score < 3:
        return True
    if request_like and len(stripped) < 160 and detail_score < 4:
        return True
    return False


def enhance_prompt_text(req: EnhanceRequest) -> tuple[str, bool, str]:
    media_type = infer_media_type(req.prompt, req.media_type)
    original = req.prompt.strip()
    if not should_enhance_prompt(original, req.strength):
        return original, False, "当前提示词可直接生成；如需更强风格、镜头、光影或负面限制，可以继续扩写。"

    negative = req.negative_prompt or "水印、低清晰度、畸形、错误文字"
    if media_type == "video":
        enhanced = (
            f"{original}。画面要求：主体动作清晰，运动自然连贯，镜头稳定，有轻微电影感推进或跟随；"
            "保留用户原始情绪和氛围，不要只堆技术镜头参数；光影自然，时间推进顺滑，画面无明显跳变。"
        )
        return enhanced, True, "已补充视频镜头、运动、节奏和氛围信息，并保留原始语义。"

    style = req.style or "高质量视觉"
    enhanced = (
        f"{style}，{original}，主体清晰，构图完整，光影层次丰富，色彩协调，细节自然，"
        f"适合高质量图片生成。不要：{negative}。"
    )
    return enhanced, True, "已补充主体、构图、光影、质感和负面限制。"


def build_route_response(req: RouteRequest) -> dict[str, Any]:
    media_type = infer_media_type(req.prompt, req.media_type)
    images = req.images or []
    size = req.size or choose_default_size(req.prompt, media_type)

    if media_type == "video":
        input_mode = infer_video_input_mode(req.prompt, images)
        try:
            width, height = parse_size(size)
        except Exception:
            width, height, size = 1152, 768, "1152x768"
        return {
            "media_type": "video",
            "model": "agnes-video-v2.0",
            "input_mode": input_mode,
            "size": size,
            "width": width,
            "height": height,
            "num_frames": 121,
            "frame_rate": 24,
            "prompt_enhancement_recommended": should_enhance_prompt(req.prompt, "auto"),
            "notes": "视频默认异步提交，完成后轮询 /v1/videos/{task_id}；生成结果会尽量本地化到 /generated/。",
        }

    model = choose_image_model(req.prompt, req.requested_model)
    return {
        "media_type": "image",
        "model": model,
        "uses_default_chain": model is None,
        "size": size,
        "response_format": "url",
        "prompt_enhancement_recommended": should_enhance_prompt(req.prompt, "auto"),
        "notes": "model 为空表示使用默认链：kolors → qwen → flux → z-image → z-turbo → pollinations。",
    }
