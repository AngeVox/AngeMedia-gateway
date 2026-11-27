"""fnOS双架构离线依赖：统一目标标签、锁文件闭包、轮子元数据及哈希校验。"""
from __future__ import annotations

from email.parser import BytesParser
import hashlib
from pathlib import Path
import re
import subprocess
import zipfile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

ARCHITECTURES = ("x86_64", "aarch64")


def target_platforms(architecture: str) -> list[str]:
    if architecture not in ARCHITECTURES:
        raise ValueError("不支持的fnOS架构")
    # CPython3.12及glibc2.28上限；PEP600标签须包含更早兼容版本，不能仅指定最高标签。
    platforms = [f"manylinux_2_{version}_{architecture}" for version in range(28, 4, -1)]
    platforms.append(f"manylinux2014_{architecture}")
    if architecture == "x86_64":
        platforms.extend(("manylinux2010_x86_64", "manylinux1_x86_64"))
    return platforms


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def parse_expected_wheels(path: Path) -> dict[str, str]:
    expected = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[a-f0-9]{64}", parts[0]):
            raise RuntimeError("轮子哈希清单格式非法")
        digest, filename = parts
        if Path(filename).name != filename or "/" in filename or "\\" in filename or not filename.endswith(".whl"):
            raise RuntimeError("轮子文件名非法")
        if filename in expected:
            raise RuntimeError(f"轮子哈希重复：{filename}")
        expected[filename] = digest
    if not expected:
        raise RuntimeError("轮子哈希清单为空")
    return expected


def download_wheels(python: str, lock: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for architecture in ARCHITECTURES:
        command = [python, "-m", "pip", "--isolated", "download", "--disable-pip-version-check",
                   "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--python-version", "312",
                   "--implementation", "cp", "--abi", "cp312", "--dest", str(destination)]
        for platform in target_platforms(architecture):
            command.extend(("--platform", platform))
        subprocess.run([*command, "--requirement", str(lock)], check=True)


def locked_requirements(lock: Path, architecture: str) -> tuple[dict[str, Requirement], dict[str, str]]:
    environment = {**default_environment(), "python_version": "3.12", "python_full_version": "3.12.0",
                   "sys_platform": "linux", "os_name": "posix", "platform_system": "Linux",
                   "platform_machine": architecture, "implementation_name": "cpython",
                   "implementation_version": "3.12.0", "platform_python_implementation": "CPython", "extra": ""}
    locked = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        specifiers = list(requirement.specifier)
        if requirement.url or len(specifiers) != 1 or specifiers[0].operator != "==" or "*" in specifiers[0].version:
            raise RuntimeError("正式锁必须为无URL的精确版本")
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        name = canonicalize_name(requirement.name)
        if name in locked:
            raise RuntimeError(f"正式锁包含重复依赖：{name}")
        locked[name] = requirement
    return locked, environment


def verify_wheelhouse(source: Path, expected: dict[str, str], lock: Path) -> dict[str, int]:
    actual = {path.name for path in source.glob("*.whl")}
    if actual != set(expected):
        raise RuntimeError(f"轮子集合不符：missing={sorted(set(expected)-actual)}, extra={sorted(actual-set(expected))}")
    wheels = []
    for filename in sorted(expected):
        wheel = source / filename
        if sha256(wheel) != expected[filename]:
            raise RuntimeError(f"轮子哈希不符：{filename}")
        name, version, _build, tags = parse_wheel_filename(filename)
        with zipfile.ZipFile(wheel) as archive:
            entries = [entry for entry in archive.namelist() if entry.endswith(".dist-info/METADATA")]
            if len(entries) != 1:
                raise RuntimeError(f"轮子元数据不唯一：{filename}")
            metadata = BytesParser().parsebytes(archive.read(entries[0]))
        if canonicalize_name(metadata["Name"] or "") != name or metadata["Version"] != str(version):
            raise RuntimeError(f"轮子名称/版本元数据不符：{filename}")
        if not SpecifierSet(metadata["Requires-Python"] or "").contains("3.12"):
            raise RuntimeError(f"轮子不支持目标Python：{filename}")
        wheels.append((filename, name, version, tags, metadata))
    supported = set()
    counts = {}
    for architecture in ARCHITECTURES:
        platforms = target_platforms(architecture)
        tags = set(cpython_tags((3, 12), platforms=platforms)) | set(compatible_tags((3, 12), interpreter="cp312", platforms=platforms))
        locked, environment = locked_requirements(lock, architecture)
        selected = {}
        for filename, name, version, wheel_tags, metadata in wheels:
            if wheel_tags & tags:
                if name not in locked or version not in locked[name].specifier:
                    raise RuntimeError(f"轮子不在正式锁内：{filename}")
                supported.add(filename)
                if name in selected:
                    raise RuntimeError(f"目标架构存在重复候选：{architecture}/{name}")
                selected[name] = (version, metadata)
        if set(selected) != set(locked):
            raise RuntimeError(f"目标架构缺少轮子：{architecture}/{sorted(set(locked)-set(selected))}")
        pending = [(name, extra) for name, req in locked.items() for extra in ({""} | set(req.extras))]
        visited = set()
        while pending:
            name, extra = pending.pop()
            if (name, extra) in visited:
                continue
            visited.add((name, extra))
            for raw in selected[name][1].get_all("Requires-Dist", []):
                requirement = Requirement(raw)
                if requirement.marker and not requirement.marker.evaluate({**environment, "extra": extra}):
                    continue
                dependency = canonicalize_name(requirement.name)
                if requirement.url or dependency not in selected or selected[dependency][0] not in requirement.specifier:
                    raise RuntimeError(f"轮子依赖闭包不完整：{architecture}/{name} -> {requirement}")
                pending.extend((dependency, value) for value in requirement.extras)
        counts[architecture] = len(selected)
    if supported != actual:
        raise RuntimeError(f"轮子不属于目标架构：{sorted(actual-supported)}")
    return counts
