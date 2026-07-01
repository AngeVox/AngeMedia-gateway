"""AngeMedia Gateway 配置。"""
from __future__ import annotations

import os
from pathlib import Path


def env_or_default(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value


def path_from_env(name: str, default: str) -> Path:
    return Path(os.path.expanduser(env_or_default(name, default)))


def env_int(name: str, default: str) -> int:
    value = env_or_default(name, default)
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须是整数，当前值：{value!r}") from exc


def env_float(name: str, default: str) -> float:
    value = env_or_default(name, default)
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须是数字，当前值：{value!r}") from exc


def env_bool(name: str, default: str) -> bool:
    value = env_or_default(name, default).strip().lower()
    if value in {"1", "true", "yes", "on", "y"}:
        return True
    if value in {"0", "false", "no", "off", "n"}:
        return False
    raise RuntimeError(f"环境变量 {name} 必须是布尔值，当前值：{value!r}")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "app" / "www"

HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PORT = env_int("PROXY_PORT", "9890")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://localhost:{PORT}").rstrip("/")

STATE_DIR = path_from_env("IMAGE_PROXY_STATE_DIR", os.path.expanduser("~/.image-proxy"))
QUOTA_FILE = path_from_env("QUOTA_FILE", str(STATE_DIR / "quota_state.json"))
OUTPUT_DIR = path_from_env("OUTPUT_DIR", str(STATE_DIR / "generated"))
UPLOAD_DIR = path_from_env("UPLOAD_DIR", str(STATE_DIR / "uploads"))
DB_FILE = path_from_env("ANGEMEDIA_DB_FILE", str(STATE_DIR / "angemedia.db"))

AUTO_DOWNLOAD_GENERATED = env_bool("AUTO_DOWNLOAD_GENERATED", "true")
LOCALIZE_STRICT = env_bool("LOCALIZE_STRICT", "false")
MEDIA_DOWNLOAD_MAX_BYTES = env_int("MEDIA_DOWNLOAD_MAX_BYTES", str(300 * 1024 * 1024))
MEDIA_DOWNLOAD_CONNECT_TIMEOUT = env_float("MEDIA_DOWNLOAD_CONNECT_TIMEOUT", "10")
MEDIA_DOWNLOAD_READ_TIMEOUT = env_float("MEDIA_DOWNLOAD_READ_TIMEOUT", "60")
MEDIA_DOWNLOAD_WRITE_TIMEOUT = env_float("MEDIA_DOWNLOAD_WRITE_TIMEOUT", "30")
MEDIA_DOWNLOAD_POOL_TIMEOUT = env_float("MEDIA_DOWNLOAD_POOL_TIMEOUT", "5")
MEDIA_DOWNLOAD_CONCURRENCY = env_int("MEDIA_DOWNLOAD_CONCURRENCY", "1")
UPLOAD_MAX_FILES = env_int("UPLOAD_MAX_FILES", "10")

MODELSCOPE_DAILY_LIMIT = env_int("MODELSCOPE_DAILY_LIMIT", "50")
MODELSCOPE_SUBMIT_TASK_TYPE = os.getenv("MODELSCOPE_SUBMIT_TASK_TYPE", "text-to-image-generation")
MODELSCOPE_POLL_TASK_TYPE = os.getenv("MODELSCOPE_POLL_TASK_TYPE", "image_generation")
MAX_POLL_TIME = env_int("MAX_POLL_TIME", "120")
POLL_INTERVAL = env_float("POLL_INTERVAL", "3")
IMAGE_PROVIDER_TIMEOUT = env_float("IMAGE_PROVIDER_TIMEOUT", env_or_default("HTTP_TIMEOUT", "60"))
HTTP_TIMEOUT = IMAGE_PROVIDER_TIMEOUT

MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY", "").strip()
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "").strip()
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "").strip()

OPENAI_IMAGE_API_KEY = os.getenv("OPENAI_IMAGE_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip()
OPENAI_IMAGE_BASE_URL = os.getenv("OPENAI_IMAGE_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")

AGNES_API_KEY = os.getenv("AGNES_API_KEY", "").strip()
AGNES_BASE_URL = os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")
AGNES_IMAGE_MODEL = os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash")
VIDEO_PROVIDER_TIMEOUT = env_float("VIDEO_PROVIDER_TIMEOUT", env_or_default("AGNES_VIDEO_SUBMIT_TIMEOUT", "900"))
AGNES_VIDEO_SUBMIT_TIMEOUT = VIDEO_PROVIDER_TIMEOUT
AGNES_VIDEO_MAX_POLL_TIME = env_int("AGNES_VIDEO_MAX_POLL_TIME", str(max(900, int(VIDEO_PROVIDER_TIMEOUT))))
AGNES_VIDEO_POLL_INTERVAL = env_float("AGNES_VIDEO_POLL_INTERVAL", "5")

BYTEDANCE_API_KEY = os.getenv("BYTEDANCE_API_KEY", "").strip()
BYTEDANCE_BASE_URL = os.getenv(
    "BYTEDANCE_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3"
).rstrip("/")

KOLORS_SIZES = {
    "1024x1024",
    "1024x2048",
    "1536x2048",
    "2048x1152",
    "960x1280",
    "768x1024",
    "720x1440",
    "720x1280",
}
MODELSCOPE_MODELS = [
    "Qwen/Qwen-Image-2512",
    "black-forest-labs/FLUX.1-Krea-dev",
    "Tongyi-MAI/Z-Image",
    "Tongyi-MAI/Z-Image-Turbo",
]
POLLINATIONS_DEFAULT_MODEL = os.getenv("POLLINATIONS_MODEL", "zimage")

BUILTIN_PROVIDER_SILICONFLOW_ENABLED = env_bool("BUILTIN_PROVIDER_SILICONFLOW_ENABLED", "true")
BUILTIN_PROVIDER_MODELSCOPE_ENABLED = env_bool("BUILTIN_PROVIDER_MODELSCOPE_ENABLED", "true")
BUILTIN_PROVIDER_POLLINATIONS_ENABLED = env_bool("BUILTIN_PROVIDER_POLLINATIONS_ENABLED", "false")
BUILTIN_PROVIDER_OPENAI_IMAGE_ENABLED = env_bool("BUILTIN_PROVIDER_OPENAI_IMAGE_ENABLED", "true")
BUILTIN_PROVIDER_AGNES_IMAGE_ENABLED = env_bool("BUILTIN_PROVIDER_AGNES_IMAGE_ENABLED", "true")
BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED = env_bool("BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED", "true")
BUILTIN_PROVIDER_BYTEDANCE_ENABLED = env_bool("BUILTIN_PROVIDER_BYTEDANCE_ENABLED", "false")

CONFIG_KEYS = {
    "PUBLIC_BASE_URL",
    "SILICONFLOW_API_KEY",
    "MODELSCOPE_API_KEY",
    "POLLINATIONS_API_KEY",
    "AGNES_API_KEY",
    "AGNES_BASE_URL",
    "BYTEDANCE_API_KEY",
    "BYTEDANCE_BASE_URL",
    "OPENAI_IMAGE_API_KEY",
    "OPENAI_IMAGE_BASE_URL",
    "OPENAI_IMAGE_MODEL",
    "GATEWAY_API_KEY",
    "AUTO_DOWNLOAD_GENERATED",
    "LOCALIZE_STRICT",
    "IMAGE_PROVIDER_TIMEOUT",
    "VIDEO_PROVIDER_TIMEOUT",
    "HTTP_TIMEOUT",
    "AGNES_VIDEO_SUBMIT_TIMEOUT",
    "MEDIA_DOWNLOAD_MAX_BYTES",
    "UPLOAD_MAX_FILES",
    "ANGE_ASSISTANT_ENABLED",
    "ANGE_LLM_API_KEY",
    "ANGE_LLM_BASE_URL",
    "ANGE_LLM_MODEL",
    "ANGE_LLM_TEMPERATURE",
    "ANGE_LLM_TIMEOUT",
    "ANGE_ASSISTANT_ALLOW_PAID",
    "ANGE_ASSISTANT_ALLOW_AGNES",
    "ANGE_ASSISTANT_CONFIRM_PLAN",
    "BUILTIN_PROVIDER_SILICONFLOW_ENABLED",
    "BUILTIN_PROVIDER_MODELSCOPE_ENABLED",
    "BUILTIN_PROVIDER_POLLINATIONS_ENABLED",
    "BUILTIN_PROVIDER_OPENAI_IMAGE_ENABLED",
    "BUILTIN_PROVIDER_AGNES_IMAGE_ENABLED",
    "BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED",
    "BUILTIN_PROVIDER_BYTEDANCE_ENABLED",
}
SECRET_KEYS = {
    "SILICONFLOW_API_KEY",
    "MODELSCOPE_API_KEY",
    "POLLINATIONS_API_KEY",
    "AGNES_API_KEY",
    "BYTEDANCE_API_KEY",
    "OPENAI_IMAGE_API_KEY",
    "GATEWAY_API_KEY",
    "ANGE_LLM_API_KEY",
}


def update_runtime(settings: dict[str, str]) -> None:
    """把 DB / 管理后台配置应用到当前进程。"""
    globals_map = globals()
    image_timeout_value = settings.get("IMAGE_PROVIDER_TIMEOUT") or settings.get("HTTP_TIMEOUT")
    video_timeout_value = settings.get("VIDEO_PROVIDER_TIMEOUT") or settings.get("AGNES_VIDEO_SUBMIT_TIMEOUT")
    for key, value in settings.items():
        if key not in CONFIG_KEYS:
            continue
        if key in {"IMAGE_PROVIDER_TIMEOUT", "VIDEO_PROVIDER_TIMEOUT", "HTTP_TIMEOUT", "AGNES_VIDEO_SUBMIT_TIMEOUT"}:
            continue
        if key in {
            "AUTO_DOWNLOAD_GENERATED",
            "LOCALIZE_STRICT",
            "BUILTIN_PROVIDER_SILICONFLOW_ENABLED",
            "BUILTIN_PROVIDER_MODELSCOPE_ENABLED",
            "BUILTIN_PROVIDER_POLLINATIONS_ENABLED",
            "BUILTIN_PROVIDER_OPENAI_IMAGE_ENABLED",
            "BUILTIN_PROVIDER_AGNES_IMAGE_ENABLED",
            "BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED",
            "BUILTIN_PROVIDER_BYTEDANCE_ENABLED",
        }:
            globals_map[key] = str(value).strip().lower() in {"1", "true", "yes", "on"}
        elif key in {"MEDIA_DOWNLOAD_MAX_BYTES", "UPLOAD_MAX_FILES"}:
            try:
                globals_map[key] = int(value)
            except ValueError:
                pass
        elif key in globals_map:
            if key.endswith("_BASE_URL"):
                globals_map[key] = str(value).rstrip("/")
            else:
                globals_map[key] = str(value).strip()
    if image_timeout_value is not None:
        try:
            image_timeout = float(image_timeout_value)
        except ValueError:
            pass
        else:
            globals_map["IMAGE_PROVIDER_TIMEOUT"] = image_timeout
            globals_map["HTTP_TIMEOUT"] = image_timeout
    if video_timeout_value is not None:
        try:
            video_timeout = float(video_timeout_value)
        except ValueError:
            pass
        else:
            globals_map["VIDEO_PROVIDER_TIMEOUT"] = video_timeout
            globals_map["AGNES_VIDEO_SUBMIT_TIMEOUT"] = video_timeout
