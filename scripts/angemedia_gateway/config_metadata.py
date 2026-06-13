"""管理后台配置元数据与保存前校验。"""
from __future__ import annotations

import urllib.parse
from typing import Any

from fastapi import HTTPException

from . import config as C


BOOL_KEYS = {
    "AUTO_DOWNLOAD_GENERATED",
    "LOCALIZE_STRICT",
    "ANGE_ASSISTANT_ENABLED",
    "ANGE_ASSISTANT_ALLOW_PAID",
    "ANGE_ASSISTANT_ALLOW_AGNES",
    "ANGE_ASSISTANT_CONFIRM_PLAN",
    "BUILTIN_PROVIDER_SILICONFLOW_ENABLED",
    "BUILTIN_PROVIDER_MODELSCOPE_ENABLED",
    "BUILTIN_PROVIDER_POLLINATIONS_ENABLED",
    "BUILTIN_PROVIDER_OPENAI_IMAGE_ENABLED",
    "BUILTIN_PROVIDER_AGNES_IMAGE_ENABLED",
    "BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED",
}

INT_KEYS = {
    "MEDIA_DOWNLOAD_MAX_BYTES": (1, 5 * 1024 * 1024 * 1024),
    "UPLOAD_MAX_FILES": (1, 100),
}

FLOAT_KEYS = {
    "ANGE_LLM_TEMPERATURE": (0.0, 2.0),
    "ANGE_LLM_TIMEOUT": (1.0, 600.0),
}

HTTP_URL_KEYS = {
    "PUBLIC_BASE_URL",
    "AGNES_BASE_URL",
    "OPENAI_IMAGE_BASE_URL",
    "ANGE_LLM_BASE_URL",
}

TRUE_VALUES = {"1", "true", "yes", "on", "y"}
FALSE_VALUES = {"0", "false", "no", "off", "n"}


CONFIG_GROUPS: list[dict[str, Any]] = [
    {
        "id": "gateway",
        "title": "基础网关与本地化",
        "description": "控制访问密钥、公开访问地址、生成文件下载和上传限制。",
        "fields": [
            {
                "key": "GATEWAY_API_KEY",
                "label": "网关访问密钥",
                "description": "保护图片、视频和辅助 API。公网或多人使用时必须配置。",
                "placeholder": "留空表示当前不启用；可点击“生成网关密钥”自动创建",
                "kind": "secret",
                "secret": True,
                "required": False,
            },
            {
                "key": "PUBLIC_BASE_URL",
                "label": "公开访问地址",
                "description": "返回给 Agent 或用户的图片、视频地址前缀。跨设备访问时请填局域网 IP 或域名。",
                "placeholder": "例如：http://192.168.1.10:9890",
                "kind": "url",
                "secret": False,
                "required": False,
            },
            {
                "key": "AUTO_DOWNLOAD_GENERATED",
                "label": "自动保存生成文件",
                "description": "把远端临时图片或视频下载到本地 /generated，减少链接过期问题。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
            {
                "key": "LOCALIZE_STRICT",
                "label": "本地化失败即终止",
                "description": "开启后，远端文件下载失败会让生成请求失败；关闭时会退回远端 URL。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
            {
                "key": "MEDIA_DOWNLOAD_MAX_BYTES",
                "label": "单个媒体最大下载体积",
                "description": "限制远端图片或视频本地化下载大小，单位是字节。",
                "placeholder": "314572800",
                "kind": "int",
                "secret": False,
                "required": False,
            },
            {
                "key": "UPLOAD_MAX_FILES",
                "label": "单次最多上传文件数",
                "description": "限制 Studio 多图上传接口一次可接收的文件数量。",
                "placeholder": "10",
                "kind": "int",
                "secret": False,
                "required": False,
            },
        ],
    },
    {
        "id": "built_in",
        "title": "内置渠道（图片生成）",
        "description": "配置默认图片生成链路。至少配置 SiliconFlow 或 ModelScope 之一即可开始测试。",
        "fields": [
            {
                "key": "SILICONFLOW_API_KEY",
                "label": "SiliconFlow 密钥",
                "description": "启用 kolors / siliconflow 模型别名，适合通用文生图。",
                "placeholder": "sk-...",
                "kind": "secret",
                "secret": True,
                "required": False,
            },
            {
                "key": "BUILTIN_PROVIDER_SILICONFLOW_ENABLED",
                "label": "启用 SiliconFlow",
                "description": "关闭后 kolors 会从可用模型与默认链路中移除，密钥仍保留。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
            {
                "key": "MODELSCOPE_API_KEY",
                "label": "ModelScope 访问令牌",
                "description": "启用 qwen、flux、z-image、z-turbo 等模型别名。",
                "placeholder": "ms-...",
                "kind": "secret",
                "secret": True,
                "required": False,
            },
            {
                "key": "BUILTIN_PROVIDER_MODELSCOPE_ENABLED",
                "label": "启用 ModelScope",
                "description": "关闭后 qwen、flux、z-image、z-turbo 会从默认链路中移除。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
            {
                "key": "POLLINATIONS_API_KEY",
                "label": "Pollinations 密钥",
                "description": "Pollinations 实验性图片渠道密钥；默认关闭，仅在显式启用并指定 model=pollinations 时使用。",
                "placeholder": "可选",
                "kind": "secret",
                "secret": True,
                "required": False,
            },
            {
                "key": "BUILTIN_PROVIDER_POLLINATIONS_ENABLED",
                "label": "启用 Pollinations 实验渠道",
                "description": "Pollinations 实验性图片渠道，默认关闭；仅在显式启用并指定 model=pollinations 时使用。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
        ],
    },
    {
        "id": "agnes",
        "title": "Agnes 图片与视频",
        "description": "启用 Agnes 图片别名和视频任务接口。该渠道通常需要单独额度。",
        "fields": [
            {
                "key": "AGNES_API_KEY",
                "label": "Agnes 密钥",
                "description": "启用 agnes-image、agnes-2.1、agnes-2.0 和 /v1/videos。",
                "placeholder": "sk-...",
                "kind": "secret",
                "secret": True,
                "required": False,
            },
            {
                "key": "AGNES_BASE_URL",
                "label": "Agnes API 地址",
                "description": "Agnes 接口根地址。通常保持默认即可。",
                "placeholder": "https://apihub.agnes-ai.com/v1",
                "kind": "url",
                "secret": False,
                "required": False,
            },
            {
                "key": "BUILTIN_PROVIDER_AGNES_IMAGE_ENABLED",
                "label": "启用 Agnes 图片",
                "description": "关闭后 agnes-image、agnes-2.1、agnes-2.0 图片别名不可用，密钥仍保留。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
            {
                "key": "BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED",
                "label": "启用 Agnes 视频",
                "description": "关闭后 /v1/videos 暂停提交新视频任务，已存在任务仍可查看历史。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
        ],
    },
    {
        "id": "openai_image",
        "title": "OpenAI-compatible 图片",
        "description": "显式调用 gpt-image-2 / openai-image 时使用，不进入默认免费降级链。",
        "fields": [
            {
                "key": "OPENAI_IMAGE_API_KEY",
                "label": "图片接口密钥",
                "description": "兼容 OpenAI Images 的密钥。留空时该渠道显示未配置。",
                "placeholder": "sk-...",
                "kind": "secret",
                "secret": True,
                "required": False,
            },
            {
                "key": "OPENAI_IMAGE_BASE_URL",
                "label": "图片接口地址",
                "description": "兼容 OpenAI Images 的 v1 根地址。",
                "placeholder": "https://api.openai.com/v1",
                "kind": "url",
                "secret": False,
                "required": False,
            },
            {
                "key": "OPENAI_IMAGE_MODEL",
                "label": "默认图片模型",
                "description": "显式调用 openai-image 时实际转发的模型名。",
                "placeholder": "gpt-image-2",
                "kind": "text",
                "secret": False,
                "required": False,
            },
            {
                "key": "BUILTIN_PROVIDER_OPENAI_IMAGE_ENABLED",
                "label": "启用 OpenAI-compatible 图片",
                "description": "关闭后 gpt-image-2 / openai-image 别名不可用，自定义渠道不受影响。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
        ],
    },
    {
        "id": "assistant",
        "title": "Ange 小助手",
        "description": "可选 LLM 规划器，用于生成前的路由、参数和提示词计划；关闭时使用规则版。",
        "fields": [
            {
                "key": "ANGE_ASSISTANT_ENABLED",
                "label": "启用小助手",
                "description": "开启后 Studio 可调用 LLM 做媒体生成计划。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
            {
                "key": "ANGE_LLM_API_KEY",
                "label": "LLM 密钥",
                "description": "小助手调用 OpenAI-compatible Chat Completions 的密钥。",
                "placeholder": "sk-...",
                "kind": "secret",
                "secret": True,
                "required": False,
            },
            {
                "key": "ANGE_LLM_BASE_URL",
                "label": "LLM 接口地址",
                "description": "OpenAI-compatible Chat Completions 的 v1 根地址，可填本地或远端服务。",
                "placeholder": "https://api.openai.com/v1",
                "kind": "url",
                "secret": False,
                "required": False,
            },
            {
                "key": "ANGE_LLM_MODEL",
                "label": "LLM 模型",
                "description": "用于生成媒体计划的聊天模型名。",
                "placeholder": "gpt-4o-mini",
                "kind": "text",
                "secret": False,
                "required": False,
            },
            {
                "key": "ANGE_LLM_TEMPERATURE",
                "label": "创意温度",
                "description": "建议 0 到 1 之间；越高计划越发散。",
                "placeholder": "0.35",
                "kind": "float",
                "secret": False,
                "required": False,
            },
            {
                "key": "ANGE_LLM_TIMEOUT",
                "label": "请求超时秒数",
                "description": "小助手等待 LLM 返回计划的最长时间。",
                "placeholder": "60",
                "kind": "float",
                "secret": False,
                "required": False,
            },
            {
                "key": "ANGE_ASSISTANT_ALLOW_PAID",
                "label": "允许选择付费模型",
                "description": "关闭时小助手不会自动选择 gpt-image-2 等付费图片模型。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
            {
                "key": "ANGE_ASSISTANT_ALLOW_AGNES",
                "label": "允许选择 Agnes",
                "description": "关闭时小助手不会自动规划 Agnes 图片或视频渠道。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
            {
                "key": "ANGE_ASSISTANT_CONFIRM_PLAN",
                "label": "生成前先确认计划",
                "description": "开启后小助手只返回计划，由用户确认后再生成。",
                "kind": "bool",
                "secret": False,
                "required": False,
            },
        ],
    },
]


def metadata_response() -> dict[str, Any]:
    """返回前端渲染配置中心所需的中文元数据。"""
    known_fields = {field["key"] for group in CONFIG_GROUPS for field in group["fields"]}
    return {
        "groups": CONFIG_GROUPS,
        "secret_keys": sorted(C.SECRET_KEYS),
        "known_fields": sorted(known_fields),
    }


def normalize_bool(key: str, value: Any) -> str:
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return "true"
    if text in FALSE_VALUES:
        return "false"
    raise HTTPException(status_code=400, detail=f"{key} 必须是布尔值：true 或 false")


def validate_http_url(key: str, value: str) -> str:
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"{key} 必须是完整的 http/https 地址")
    return value.rstrip("/")


def validate_config_settings(settings: dict[str, Any]) -> dict[str, str]:
    """保存配置前统一校验，避免非法值被静默写入。"""
    normalized: dict[str, str] = {}
    for key, value in settings.items():
        if key not in C.CONFIG_KEYS:
            raise HTTPException(status_code=400, detail=f"不支持的配置项：{key}")
        text = str(value).strip()
        if key in BOOL_KEYS:
            normalized[key] = normalize_bool(key, text)
            continue
        if key in INT_KEYS:
            if not text:
                normalized[key] = ""
                continue
            try:
                number = int(text)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{key} 必须是整数") from exc
            min_value, max_value = INT_KEYS[key]
            if number < min_value or number > max_value:
                raise HTTPException(status_code=400, detail=f"{key} 必须在 {min_value} 到 {max_value} 之间")
            normalized[key] = str(number)
            continue
        if key in FLOAT_KEYS:
            if not text:
                normalized[key] = ""
                continue
            try:
                number = float(text)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{key} 必须是数字") from exc
            min_value, max_value = FLOAT_KEYS[key]
            if number < min_value or number > max_value:
                raise HTTPException(status_code=400, detail=f"{key} 必须在 {min_value} 到 {max_value} 之间")
            normalized[key] = str(number)
            continue
        if key in HTTP_URL_KEYS:
            normalized[key] = validate_http_url(key, text)
            continue
        normalized[key] = text
    return normalized
