"""阻止命令行用其他运行账号打开 SQLite 数据库。"""
from __future__ import annotations

import os
from pathlib import Path


class PreflightDatabaseOwnerMismatch(Exception):
    """盘点账号与主库或 WAL 辅助文件属主不一致。"""


def preflight_database_path(path: str | Path) -> Path:
    target = Path(path).resolve(strict=True)
    if not target.is_file():
        raise ValueError('preflight_database_unavailable')

    get_uid = getattr(os, 'geteuid', None)
    if get_uid is None:
        return target
    uid = get_uid()
    if target.stat().st_uid != uid:
        # sidecar 即使在检查时已存在，也可能在 SQLite 连接前被正常清理。
        raise PreflightDatabaseOwnerMismatch()
    for suffix in ('-wal', '-shm'):
        try:
            owner = target.with_name(target.name + suffix).stat().st_uid
        except FileNotFoundError:
            continue
        if owner != uid:
            raise PreflightDatabaseOwnerMismatch()
    return target
