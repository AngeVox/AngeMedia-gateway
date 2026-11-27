"""视频先写入隐藏的暂存区，入库时再在本地原子更名。"""
from contextlib import closing, contextmanager
from pathlib import Path
import re
from uuid import uuid4

from .. import config as C
from ..db.connection import db_connect

_FOLDER = ".pollinations-video"
_STAGING = "staged"
_PUBLIC_NAME = re.compile(r"[a-f0-9]{32}\.mp4")


def pollinations_video_file(job_id, *, staged=False):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Invalid video job identity")
    root = C.OUTPUT_DIR.resolve()
    directory = root / _FOLDER / _STAGING if staged else root / _FOLDER
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve() != directory:
        raise ValueError("Invalid video output directory")
    path = directory / f"{job_id}.mp4"
    if path.is_symlink():
        raise ValueError("Invalid video output path")
    return path


@contextmanager
def temporary_video_file(job_id):
    path = pollinations_video_file(job_id, staged=True)
    temporary = path.with_name(f"{path.stem}.{uuid4().hex}.part")
    try:
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def pollinations_video_visible(path: Path):
    parts = path.relative_to(C.OUTPUT_DIR.resolve()).parts
    if not parts or parts[0] != _FOLDER:
        return True
    if len(parts) != 2 or not _PUBLIC_NAME.fullmatch(parts[1]):
        return False
    with closing(db_connect()) as conn:
        return conn.execute(
            "SELECT 1 FROM assets WHERE storage_area='output' AND relative_path=? AND provider='pollinations'",
            (path.relative_to(C.OUTPUT_DIR.resolve()).as_posix(),),
        ).fetchone() is not None
