"""连接证据密钥与 SQLite 分开保管，供同一部署的进程共用。"""
from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from .. import config as C
from ..storage_files import storage_directory


class EvidenceKeyUnavailable(RuntimeError):
    """状态目录不能安全保存连接证据密钥。"""


_KEY_NAME = ".provider-evidence-key"


def _read_key(root: Path) -> bytes:
    with storage_directory(root) as directory, directory.read(_KEY_NAME) as stream:
        if os.name != "nt" and os.fstat(stream.fileno()).st_mode & 0o077:
            raise EvidenceKeyUnavailable()
        key = stream.read(33)
    if len(key) != 32:
        raise EvidenceKeyUnavailable()
    return key


def load_evidence_key(*, create: bool = False) -> bytes | None:
    root = C.DB_FILE.parent.resolve()
    # 密钥不得放在可能由媒体路由公开的目录下。
    if any(root.is_relative_to(base.resolve()) for base in (C.OUTPUT_DIR, C.UPLOAD_DIR)):
        return None
    try:
        try:
            return _read_key(root)
        except FileNotFoundError:
            if not create:
                return None

        # 创建后原子发布，其他进程只会读到完整密钥。
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=root, prefix=".evidence-", delete=False) as temp:
                temporary = Path(temp.name)
                temp.write(secrets.token_bytes(32))
                temp.flush()
                os.fsync(temp.fileno())
            try:
                os.link(temporary, root / _KEY_NAME)
            except FileExistsError:
                pass
            return _read_key(root)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except (OSError, EvidenceKeyUnavailable):
        return None
