"""纯 helper 函数，不依赖 DB / config / migration。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException


PROVIDER_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def first_result_url(result: dict[str, Any]) -> tuple[str, str, str]:
    if "video_url" in result:
        return str(result.get("video_url") or ""), str(result.get("remote_video_url") or ""), str(result.get("local_path") or "")
    data = result.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        item = data[0]
        return str(item.get("url") or ""), str(item.get("remote_url") or ""), str(item.get("local_path") or "")
    return "", "", ""


def validate_provider_id(provider_id: str) -> str:
    value = provider_id.strip().lower()
    if not PROVIDER_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="渠道 ID 只能包含小写字母、数字和连字符，长度 1-64")
    return value


def is_relative_to_path(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_unlink_under(path_text: str, base_dir: Path) -> bool:
    if not path_text:
        return False
    path = Path(path_text).expanduser()
    resolved = path.resolve()
    base = base_dir.resolve()
    if not is_relative_to_path(resolved, base):
        raise HTTPException(status_code=400, detail="拒绝删除目录外文件")
    if resolved.exists() and resolved.is_file():
        try:
            resolved.unlink()
            return True
        except OSError:
            return False
    return False
