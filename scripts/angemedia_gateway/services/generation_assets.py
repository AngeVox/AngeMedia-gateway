"""Generated media asset persistence helpers."""
from __future__ import annotations

import uuid
import sqlite3
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .. import config as C
from ..job_sanitizer import sanitized_json
from ..repositories.assets import save_asset


def generated_local_paths(result: dict[str, Any], media_type: str) -> list[str]:
    if media_type == "video":
        local_path = str(result.get("local_path") or "")
        return [local_path] if local_path else []
    data = result.get("data")
    if not isinstance(data, list):
        return []
    paths: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        local_path = str(item.get("local_path") or "")
        if local_path:
            paths.append(local_path)
    return paths


def generated_output_file(local_path: str) -> Path | None:
    if not local_path:
        return None
    path = Path(local_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        resolved = path.resolve()
        resolved.relative_to(C.OUTPUT_DIR.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def generated_output_files(result: dict[str, Any], media_type: str) -> list[Path]:
    files: list[Path] = []
    for local_path in generated_local_paths(result, media_type):
        path = generated_output_file(local_path)
        if path is not None:
            files.append(path)
    return files


def save_generated_asset(
    *,
    media_type: str,
    result: dict[str, Any],
    prompt: str,
    model: str | None,
    provider: str | None,
    duration_ms: int,
    job_id: str | None = None,
    save_asset_func: Callable[..., None] = save_asset,
    conn: sqlite3.Connection | None = None,
) -> int:
    saved = 0
    for path in generated_output_files(result, media_type):
        filename = path.name
        kwargs = dict(
            id=uuid.uuid4().hex,
            filename=filename,
            storage_area="output",
            relative_path=filename,
            url_path=f"/generated/{filename}",
            media_type=media_type,
            source="generated",
            size=path.stat().st_size,
            prompt=prompt,
            model=model,
            provider=provider,
            duration_ms=duration_ms,
            job_id=job_id,
        )
        if conn is not None:
            kwargs["conn"] = conn
        save_asset_func(**kwargs)
        saved += 1
    return saved


def safe_output_json(result: dict[str, Any]) -> str:
    """Build a small output_json summary without storing full b64 content."""

    data = result.get("data")
    has_url = False
    has_b64 = False
    image_count = 0
    if isinstance(data, list):
        image_count = len(data)
        for item in data:
            if isinstance(item, dict):
                if item.get("url"):
                    has_url = True
                if item.get("b64_json"):
                    has_b64 = True
    summary: dict[str, Any] = {
        "provider": result.get("provider", ""),
        "model": result.get("model", ""),
        "history_id": result.get("history_id", ""),
        "image_count": image_count,
        "has_url": has_url,
        "has_b64_json": has_b64,
    }
    if has_url:
        first = data[0] if isinstance(data, list) and data else {}
        if isinstance(first, dict) and first.get("url"):
            parsed = urlparse(str(first["url"]))
            if not parsed.query and not parsed.fragment and parsed.path.startswith("/generated/"):
                summary["url"] = parsed.path
    return sanitized_json(summary)
