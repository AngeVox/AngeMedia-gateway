"""在已打开的存储目录内定位文件，防止校验后重走可变路径。"""
from __future__ import annotations

import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path


def storage_parts(relative: str) -> tuple[str, ...]:
    parts = tuple(relative.split("/"))
    if any(part in {"", ".", ".."} or any(char in part for char in "\\:\0") for part in parts):
        raise ValueError("invalid storage path")
    return parts


@contextmanager
def _pin_windows_path(path: Path, *, directory: bool):
    # 拒绝重解析点，并持有拒绝共享删除的句柄，直到完成读取或相对父目录操作。
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    flags = 0x00200000 | (0x02000000 if directory else 0)  # OPEN_REPARSE_POINT, BACKUP_SEMANTICS
    handle = create(str(path), 0x80000000, 3, None, 3, flags, None)  # GENERIC_READ，不允许共享删除。
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        # 重解析点会被拒绝；保留真正缺文件与不可读的区别。
        if error in {2, 3}:
            raise FileNotFoundError(error, "storage entry not found")
        raise ctypes.WinError(error)
    try:
        info = path.lstat()
        valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (not valid_type or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or path.resolve(strict=True) != path):
            raise OSError("unsafe storage entry")
        yield
    finally:
        close(handle)


class StorageDirectory:
    """目录句柄与逻辑位置，子项名称取自已校验的路径或服务端命名。"""

    def __init__(self, path: Path, fd: int | None = None):
        self.path = path
        self.fd = fd

    def _entry(self, name: str) -> Path | str:
        return self.path / name if self.fd is None else name

    @contextmanager
    def child(self, name: str, *, create: bool = False):
        if create:
            try:
                os.mkdir(self._entry(name), dir_fd=self.fd)
            except FileExistsError:
                pass
        with _open_directory(self.path / name, parent=self.fd, name=name) as child:
            yield child

    @contextmanager
    def read(self, name: str):
        path = self.path / name
        with ExitStack() as stack:
            if self.fd is None:
                stack.enter_context(_pin_windows_path(path, directory=False))
                stream = stack.enter_context(path.open("rb"))
            else:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
                try:
                    stream = os.fdopen(fd, "rb")
                except BaseException:
                    os.close(fd)
                    raise
                stack.enter_context(stream)
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError("storage entry is not a regular file")
            yield stream

    def create_file(self, name: str):
        fd = os.open(self._entry(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                     0o600, dir_fd=self.fd)
        try:
            return os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise

    def replace(self, source: str, destination: str) -> None:
        os.replace(self._entry(source), self._entry(destination), src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def names(self) -> list[str]:
        return os.listdir(self.path if self.fd is None else self.fd)

    def stat(self, name: str) -> os.stat_result:
        return os.stat(self._entry(name), follow_symlinks=False, dir_fd=self.fd)

    def unlink(self, name: str) -> bool:
        try:
            os.unlink(self._entry(name), dir_fd=self.fd)
        except FileNotFoundError:
            return False
        return True

    def rmdir(self, name: str) -> None:
        os.rmdir(self._entry(name), dir_fd=self.fd)


@contextmanager
def _open_directory(path: Path, *, parent: int | None = None, name: str | None = None):
    if os.name == "nt":
        with _pin_windows_path(path, directory=True):
            yield StorageDirectory(path)
        return
    fd = os.open(path if name is None else name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield StorageDirectory(path, fd)
    finally:
        os.close(fd)


@contextmanager
def storage_directory(root: Path, parts: tuple[str, ...] = ()):
    """固定根目录和各级子目录，防止跟随用户可替换的路径分量。"""
    with ExitStack() as stack:
        folder = stack.enter_context(_open_directory(root))
        for part in parts:
            folder = stack.enter_context(folder.child(part))
        yield folder
