"""助手消息与运行载荷的共享脱敏边界，不清除创作正文。"""
from __future__ import annotations

import json
import re
from typing import Any

from .job_sanitizer import sanitize_error_text, sanitize_job_value


def sanitize_assistant_text(value: Any, *, limit: int = 4000) -> str:
    text = sanitize_error_text(str(value or ""), limit=limit) or ""
    text = re.sub(r"(?<![A-Za-z0-9])/(?:root|home|tmp|var|mnt|vol\d*|app)(?:/[^\s,;，。)]*)?", "[redacted local path]", text)
    text = re.sub(r"\bAuthorization\b(?:\s*:\s*Bearer\s+[A-Za-z0-9_.-]+)?", "[redacted auth]", text, flags=re.I)
    text = re.sub(r"\bprovider[_ -]?raw[_ -]?body\b", "[redacted provider body]", text, flags=re.I)
    text = re.sub(r"data:[A-Za-z0-9.+/-]+;base64,(?:[A-Za-z0-9+/=_-]+|<redacted-data-url>)", "[redacted data url]", text, flags=re.I)
    text = re.sub(r"\b[A-Za-z]:\\[^\s,;，。]+", "[redacted local path]", text)
    text = re.sub(r"request_hash\s*[:=]\s*[A-Za-z0-9_.:-]+", "[redacted hash]", text, flags=re.I)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def runtime_payload(value: Any) -> str:
    """持久化只接受有界JSON，复用现有脱敏并保护租约控制凭据。"""
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(raw.encode()) > 32768:
            raise ValueError("运行载荷超过32KiB")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("运行载荷必须是32KiB以内的有效JSON") from exc

    def validate(item: Any, depth: int = 0) -> None:
        if depth > 12:
            raise ValueError("运行载荷嵌套过深")
        if isinstance(item, dict):
            if len(item) > 1000 or any(not isinstance(key, str) or len(key) > 128 for key in item):
                raise ValueError("运行载荷对象键非法或过多")
            for child in item.values():
                validate(child, depth+1)
        elif isinstance(item, (list, tuple)):
            if len(item) > 1000:
                raise ValueError("运行载荷数组过长")
            for child in item:
                validate(child, depth+1)

    validate(value)

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: "[REDACTED]" if re.sub(r"[^a-z]", "", key.lower()) in {
                "ownertoken", "leasetoken", "leaseowner", "fencingtoken", "requesthash", "providerrawbody"
            } else clean(child) for key, child in item.items()}
        if isinstance(item, list):
            return [clean(child) for child in item]
        return sanitize_assistant_text(item, limit=32768) if isinstance(item, str) else item

    result = json.dumps(clean(sanitize_job_value(value)), ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    if len(result.encode()) > 32768:
        raise ValueError("脱敏后的运行载荷超过32KiB")
    return result
