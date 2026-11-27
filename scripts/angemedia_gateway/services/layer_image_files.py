"""分层成员的有界取回和文件持久化。"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from .. import config as C
from ..layer_paths import LAYER_NAMESPACE
from ..layered_images import image_geometry
from ..media import fetch_public_remote_media
from ..storage_files import storage_directory


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


async def fetch_layer_files(job_id, entries, expires: datetime, now: Callable, commit_member: Callable) -> list[dict]:
    """每次最多持有一个成员，已保存的成员继续使用原字节。"""
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise LayerArtifactRejected()
    C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    root = C.OUTPUT_DIR.resolve()
    remaining = LAYER_GROUP_MAX_BYTES
    files = []
    limit = min(LAYER_MEMBER_MAX_BYTES, C.MEDIA_DOWNLOAD_MAX_BYTES)
    with storage_directory(root) as output, output.child(LAYER_NAMESPACE, create=True) as layers, layers.child(job_id, create=True) as folder:
        for entry in entries:
            suffix = "jpg" if entry["output_format"] == "jpeg" else "png"
            name = f'{entry["z_index"]:03d}.{suffix}'
            bound = min(remaining, limit)
            if bound <= 0:
                raise LayerArtifactRejected()
            try:
                with folder.read(name) as stream:
                    content = stream.read(bound + 1)
            except FileNotFoundError:
                if now() >= expires:
                    raise LayerDownloadExpired()
                content, _mime, _final = await fetch_public_remote_media(entry["url"], max_bytes=bound)
                if len(content) > bound:
                    raise LayerArtifactRejected()
                _check_member(content, entry)
                temporary = f'{entry["z_index"]:03d}.{uuid4().hex}.part'
                stream = folder.create_file(temporary)
                try:
                    with stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    commit_member(lambda: folder.replace(temporary, name))
                finally:
                    folder.unlink(temporary)
            else:
                if len(content) > bound:
                    raise LayerArtifactRejected()
                _check_member(content, entry)
            remaining -= len(content)
            public = {key: value for key, value in entry.items() if key != "url"}
            public["relative_path"] = f"{LAYER_NAMESPACE}/{job_id}/{name}"
            public["bytes"] = len(content)
            files.append(public)
            del content
    return files


def discard_layer_files(job_id: str) -> None:
    """任务失败或层组撤下后清理对应成员。"""
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return
    try:
        with storage_directory(C.OUTPUT_DIR.resolve(), (LAYER_NAMESPACE,)) as layers:
            with layers.child(job_id) as folder:
                for name in folder.names():
                    if re.fullmatch(r"[0-9]{3}\.(?:png|jpg|[a-f0-9]{32}\.part)", name):
                        folder.unlink(name)
            layers.rmdir(job_id)
    except (OSError, ValueError):
        # 暂存文件仍不可公开；并发退出或只读磁盘不改变已提交终态。
        return
