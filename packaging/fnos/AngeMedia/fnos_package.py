"""对最终FPK的控制文件和应用载荷核验同一份待打包字节。"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile

from fnos_wheels import sha256


def file_hashes(root: Path) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise RuntimeError(f"unsupported staged file: {path.relative_to(root)}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = sha256(path)
    return files


def manifest_fields(path: Path) -> dict[str, str]:
    fields = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key = key.strip()
        if not separator or not key or key in fields:
            raise RuntimeError('invalid or duplicate manifest field')
        fields[key] = value.strip()
    return fields


def stage_snapshot(stage: Path) -> dict:
    files = file_hashes(stage)
    return {
        'payload': {name[4:]: digest for name, digest in files.items() if name.startswith('app/')},
        'control': {name: digest for name, digest in files.items() if not name.startswith('app/') and name != 'manifest'},
        'manifest': manifest_fields(stage/'manifest'),
        'executables': {name: (stage/name).stat().st_mode & 0o111 for name in files if (stage/name).stat().st_mode & 0o111},
    }


def safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    names = {}
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        # fnpack会重复写入config；仅接受同类型、权限和内容完全一致的重复项。
        if (path.is_absolute() or '..' in path.parts or '\\' in member.name
                or not (member.isfile() or member.isdir())):
            raise RuntimeError(f"unsafe archive member: {member.name}")
        if not (destination/str(path)).resolve().is_relative_to(root):
            raise RuntimeError(f"unsafe archive member: {member.name}")
        digest = None
        if member.isfile():
            with archive.extractfile(member) as handle:
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        signature = (member.type, member.mode, digest)
        if path in names and names[path] != signature:
            raise RuntimeError(f"unsafe archive member: {member.name}")
        names[path] = signature
    archive.extractall(destination, filter='data')


def _compare(actual: dict[str, str], expected: dict[str, str], label: str) -> None:
    missing = sorted(expected.keys()-actual.keys())
    extra = sorted(actual.keys()-expected.keys())
    changed = sorted(name for name in actual.keys() & expected.keys() if actual[name] != expected[name])
    if missing or extra or changed:
        raise RuntimeError(f"packed {label} mismatch: missing={missing[:5]}, extra={extra[:5]}, changed={changed[:5]}")


def verify_package(package_file: Path, expected_wheels: int, expected: dict) -> None:
    with tempfile.TemporaryDirectory(prefix='angemedia-fnos-verify-') as temp_dir:
        root = Path(temp_dir)
        with tarfile.open(package_file, 'r:gz') as archive:
            safe_extract(archive, root)
        fields = manifest_fields(root/'manifest')
        checksum = fields.pop('checksum', '')
        if not re.fullmatch(r'[0-9a-f]{32}', checksum):
            raise RuntimeError('packed manifest checksum is missing')
        _compare(fields, expected['manifest'], 'manifest')
        app_tgz = root/'app.tgz'
        with app_tgz.open('rb') as handle:
            actual_md5 = hashlib.file_digest(handle, lambda: hashlib.md5(usedforsecurity=False)).hexdigest()
        if actual_md5 != checksum:
            raise RuntimeError('packed app checksum mismatch')
        control = {name: digest for name, digest in file_hashes(root).items() if name not in ('manifest', 'app.tgz')}
        _compare(control, expected['control'], 'control')
        app_root = root/'app-expanded'
        app_root.mkdir()
        with tarfile.open(app_tgz, 'r:gz') as archive:
            safe_extract(archive, app_root)
        payload = file_hashes(app_root)
        _compare(payload, expected['payload'], 'payload')
        paths = {name: root/name for name in control}
        paths.update({'app/'+name: app_root/name for name in payload})
        executables = {name: path.stat().st_mode & 0o111 for name, path in paths.items() if path.stat().st_mode & 0o111}
        _compare(executables, expected['executables'], 'executable permissions')
        wheels = list((app_root/'server/vendor/wheels').glob('*.whl'))
        if len(wheels) != expected_wheels:
            raise RuntimeError(f'packed wheel count mismatch: {len(wheels)} != {expected_wheels}')
        forbidden = [name for name in payload if '__pycache__' in PurePosixPath(name).parts
                     or name.endswith(('.pyc', '.pyo')) or PurePosixPath(name).name == '.DS_Store']
        if forbidden:
            raise RuntimeError(f'forbidden generated files in package: {forbidden[:10]}')
