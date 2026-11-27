"""分层成员的有界取回和文件持久化。"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .. import config as C
from ..layer_paths import LAYER_NAMESPACE
from ..layered_images import image_geometry
from ..media import fetch_public_remote_media


LAYER_MEMBER_MAX_BYTES = 25 * 1024 * 1024
LAYER_GROUP_MAX_BYTES = 512 * 1024 * 1024


class LayerDownloadExpired(RuntimeError):
    pass


class LayerArtifactRejected(RuntimeError):
    pass


def _check_member(content, entry):
    try:
        geometry = image_geometry(content)
    except ValueError:
        raise LayerArtifactRejected() from None
    if geometry != (entry["output_format"], entry["width"], entry["height"]):
        raise LayerArtifactRejected()
    if entry["z_index"] and not _png_has_alpha(content):
        raise LayerArtifactRejected()


def _png_has_alpha(content):
    if content[25] in {4, 6}:
        return True
    offset = 33
    while offset + 12 <= len(content):
        length = int.from_bytes(content[offset:offset + 4])
        kind = content[offset + 4:offset + 8]
        if kind == b"IDAT":
            return False
        offset += 12 + length
        if kind == b"tRNS":
            return length > 0 and offset <= len(content)
    return False


def _member_path(job_id, entry):
    suffix = ".jpg" if entry["output_format"] == "jpeg" else ".png"
    root = C.OUTPUT_DIR.resolve()
    folder = root / LAYER_NAMESPACE / job_id
    if folder.resolve() != folder:
        raise LayerArtifactRejected()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{entry["z_index"]:03d}{suffix}'
    if path.is_symlink():
        raise LayerArtifactRejected()
    return path


async def fetch_layer_files(job_id, entries, expires: datetime, now: Callable, commit_member: Callable) -> list[dict]:
    """每次最多持有一个成员，已经落盘的成员继续使用原字节。"""
    remaining = LAYER_GROUP_MAX_BYTES
    files = []
    limit = min(LAYER_MEMBER_MAX_BYTES, C.MEDIA_DOWNLOAD_MAX_BYTES)
    for entry in entries:
        path = _member_path(job_id, entry)
        bound = min(remaining, limit)
        if bound <= 0:
            raise LayerArtifactRejected()
        if path.is_file():
            with path.open("rb") as stream:
                content = stream.read(bound + 1)
            if len(content) > bound:
                raise LayerArtifactRejected()
            _check_member(content, entry)
        else:
            if now() >= expires:
                raise LayerDownloadExpired()
            content, _mime, _final = await fetch_public_remote_media(entry["url"], max_bytes=bound)
            _check_member(content, entry)
            temporary = path.with_suffix(f".{uuid4().hex}.part")
            try:
                with temporary.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                commit_member(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        remaining -= len(content)
        public = {key: value for key, value in entry.items() if key != "url"}
        public["relative_path"] = path.relative_to(C.OUTPUT_DIR.resolve()).as_posix()
        public["bytes"] = len(content)
        files.append(public)
        del content
    return files


def discard_layer_files(job_id: str) -> None:
    """任务失败或层组撤下后清理对应成员，保留其他路径。"""
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return
    folder = C.OUTPUT_DIR.resolve() / LAYER_NAMESPACE / job_id
    try:
        if not folder.is_dir() or folder.resolve() != folder:
            return
        for path in folder.iterdir():
            if re.fullmatch(r"[0-9]{3}\.(?:png|jpg|[a-f0-9]{32}\.part)", path.name):
                path.unlink(missing_ok=True)
        folder.rmdir()
    except OSError:
        # 暂存文件仍不可公开；并发退出的文件句柄或只读磁盘不改变已提交终态。
        return
