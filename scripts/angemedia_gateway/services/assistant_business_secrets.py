"""仅在进程内读取已配置密钥，按精确值清理正文，不做主题词过滤。"""
from __future__ import annotations

from contextlib import closing
import os

from .. import config as C
from ..db.connection import db_connect


def configured_secrets() -> tuple[str, ...]:
    values = {str(getattr(C, key, "") or os.getenv(key, "")) for key in C.SECRET_KEYS}
    with closing(db_connect()) as conn:
        values.update(str(row[0]) for row in conn.execute(
            "SELECT value FROM config WHERE key IN (" + ",".join("?" for _ in C.SECRET_KEYS) + ")", tuple(C.SECRET_KEYS)))
        for table in ("provider_runtime_configs", "custom_providers"):
            values.update(str(row[0] or "") for row in conn.execute(f"SELECT api_key FROM {table}"))
    return tuple(sorted((value for value in values if value), key=len, reverse=True))


def redact_values(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {key: redact_values(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_values(item, secrets) for item in value]
    return value
