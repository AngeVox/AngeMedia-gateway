"""纯 helper 函数，不依赖 DB / config / migration。"""
from __future__ import annotations

import json
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any

from fastapi import HTTPException

from .storage_files import storage_directory, storage_parts


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


def safe_unlink_under(path_text: str, base_dir: Path, *, raise_on_io_error: bool = False) -> bool:
    if not path_text:
        return False
    path = PurePath(path_text)
    if not path.is_absolute():
        path = Path.cwd() / path
    configured_root = base_dir.absolute()
    root = configured_root.resolve()
    try:
        relative = path.relative_to(configured_root) if path.is_relative_to(configured_root) else path.relative_to(root)
        parts = storage_parts(relative.as_posix())
    except ValueError:
        if path == root or path == configured_root:
            return False
        raise HTTPException(status_code=400, detail="拒绝删除目录外文件") from None
    try:
        with storage_directory(root, parts[:-1]) as folder:
            info = folder.stat(parts[-1])
            if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise HTTPException(status_code=400, detail="拒绝删除目录外文件")
            return stat.S_ISREG(info.st_mode) and folder.unlink(parts[-1])
    except FileNotFoundError:
        return False
    except OSError:
        if raise_on_io_error:
            raise
        return False
