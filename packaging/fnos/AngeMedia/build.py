#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_expected_wheels(checksum_file: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for raw_line in checksum_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        digest, filename = line.split(maxsplit=1)
        filename = filename.lstrip("*")
        if filename in expected:
            raise RuntimeError(f"duplicate wheel checksum entry: {filename}")
        expected[filename] = digest
    return expected


def prepare_wheelhouse(
    *,
    repo_root: Path,
    package_root: Path,
    stage_wheelhouse: Path,
    wheelhouse_source: Path | None,
    python_bin: str,
) -> None:
    checksum_file = package_root / "wheelhouse.SHA256SUMS"
    expected = parse_expected_wheels(checksum_file)
    with tempfile.TemporaryDirectory(prefix="angemedia-fnos-wheels-") as temp_dir:
        download_dir = Path(temp_dir)
        if wheelhouse_source is None:
            run(
                [
                    python_bin,
                    "-m",
                    "pip",
                    "download",
                    "--disable-pip-version-check",
                    "--only-binary=:all:",
                    "--dest",
                    str(download_dir),
                    "--requirement",
                    str(repo_root / "requirements.lock"),
                ]
            )
            source = download_dir
        else:
            source = wheelhouse_source.resolve()

        actual = {path.name for path in source.glob("*.whl")}
        wanted = set(expected)
        if actual != wanted:
            missing = sorted(wanted - actual)
            extra = sorted(actual - wanted)
            raise RuntimeError(f"wheelhouse mismatch: missing={missing}, extra={extra}")

        for filename, expected_digest in expected.items():
            wheel = source / filename
            actual_digest = sha256(wheel)
            if actual_digest != expected_digest:
                raise RuntimeError(
                    f"wheel hash mismatch for {filename}: {actual_digest} != {expected_digest}"
                )

        stage_wheelhouse.mkdir(parents=True, exist_ok=True)
        for filename in sorted(expected):
            shutil.copy2(source / filename, stage_wheelhouse / filename)
        shutil.copy2(checksum_file, stage_wheelhouse / "SHA256SUMS")


def update_manifest_version(manifest: Path, version_override: str | None) -> str:
    text = manifest.read_text(encoding="utf-8")
    match = re.search(r"^version\s*=\s*(\S+)\s*$", text, re.MULTILINE)
    if not match:
        raise RuntimeError("manifest version line is missing")
    version = version_override or match.group(1)
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]*", version):
        raise RuntimeError(f"invalid package version: {version}")
    if version_override:
        text = re.sub(
            r"^(version\s*=\s*)\S+(\s*)$",
            rf"\g<1>{version}\g<2>",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        manifest.write_text(text, encoding="utf-8", newline="\n")
    return version


def build_stage(repo_root: Path, package_root: Path, stage: Path) -> None:
    for name in ("manifest",):
        shutil.copy2(package_root / name, stage / name)
    for directory in ("cmd", "config", "wizard", "i18n"):
        copy_tree(package_root / directory, stage / directory)

    shutil.copy2(package_root / "assets" / "ICON.PNG", stage / "ICON.PNG")
    shutil.copy2(package_root / "assets" / "ICON_256.PNG", stage / "ICON_256.PNG")

    app_root = stage / "app"
    server_root = app_root / "server"
    for directory in ("app", "scripts", "docs"):
        copy_tree(repo_root / directory, server_root / directory)
    for filename in (
        ".env.example",
        "LICENSE",
        "README.md",
        "README_CN.md",
        "requirements.lock",
        "requirements.txt",
    ):
        shutil.copy2(repo_root / filename, server_root / filename)

    copy_tree(package_root / "config", app_root / "config")
    copy_tree(package_root / "app" / "ui", app_root / "ui")
    (app_root / "ui" / "images").mkdir(parents=True, exist_ok=True)
    shutil.copy2(package_root / "assets" / "ICON.PNG", app_root / "ui" / "images" / "icon_64.png")
    shutil.copy2(
        package_root / "assets" / "ICON_256.PNG",
        app_root / "ui" / "images" / "icon_256.png",
    )
    (app_root / "www").mkdir(parents=True, exist_ok=True)


def safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        member_path = Path(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise RuntimeError(f"unsafe archive member: {member.name}")
        target = (destination / member_path).resolve()
        target.relative_to(root)
        if member.issym() or member.islnk():
            link_target = (target.parent / member.linkname).resolve()
            link_target.relative_to(root)
    archive.extractall(destination)


def verify_package(package_file: Path, expected_wheels: int) -> None:
    with tempfile.TemporaryDirectory(prefix="angemedia-fnos-verify-") as temp_dir:
        root = Path(temp_dir)
        with tarfile.open(package_file, "r:gz") as archive:
            safe_extract(archive, root)
        manifest = (root / "manifest").read_text(encoding="utf-8")
        match = re.search(r"^checksum\s*=\s*([0-9a-f]{32})\s*$", manifest, re.MULTILINE)
        if not match:
            raise RuntimeError("packed manifest checksum is missing")
        app_tgz = root / "app.tgz"
        if md5(app_tgz) != match.group(1):
            raise RuntimeError("packed app checksum mismatch")
        app_root = root / "app-expanded"
        app_root.mkdir()
        with tarfile.open(app_tgz, "r:gz") as archive:
            safe_extract(archive, app_root)
        wheels = list((app_root / "server" / "vendor" / "wheels").glob("*.whl"))
        if len(wheels) != expected_wheels:
            raise RuntimeError(f"packed wheel count mismatch: {len(wheels)} != {expected_wheels}")
        forbidden = list(app_root.rglob("__pycache__")) + list(app_root.rglob("*.pyc")) + list(app_root.rglob(".DS_Store"))
        if forbidden:
            raise RuntimeError(f"forbidden generated files in package: {forbidden[:10]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the AngeMedia fnOS/FYGO x86 FPK package")
    parser.add_argument("--output-dir", type=Path, default=Path("dist/fnos"))
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--python", default=os.environ.get("PYTHON_BIN", "/var/apps/python312/target/bin/python3"))
    parser.add_argument("--version")
    args = parser.parse_args()

    package_root = Path(__file__).resolve().parent
    repo_root = package_root.parents[2]
    if shutil.which("fnpack") is None:
        raise RuntimeError("fnpack is required")
    if args.wheelhouse is None and not Path(args.python).exists() and shutil.which(args.python) is None:
        raise RuntimeError(f"Python executable is unavailable: {args.python}")

    expected_wheels = parse_expected_wheels(package_root / "wheelhouse.SHA256SUMS")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="angemedia-fnos-build-") as temp_dir:
        stage = Path(temp_dir) / "stage"
        stage.mkdir()
        build_stage(repo_root, package_root, stage)
        version = update_manifest_version(stage / "manifest", args.version)
        prepare_wheelhouse(
            repo_root=repo_root,
            package_root=package_root,
            stage_wheelhouse=stage / "app" / "server" / "vendor" / "wheels",
            wheelhouse_source=args.wheelhouse,
            python_bin=args.python,
        )
        run(["fnpack", "build"], cwd=stage)
        built = stage / "AngeMedia.fpk"
        final = output_dir / f"AngeMedia-v{version}-fnOS-x86.fpk"
        shutil.copy2(built, final)
        verify_package(final, len(expected_wheels))
        checksum_file = final.with_suffix(final.suffix + ".sha256")
        checksum_file.write_text(f"{sha256(final)}  {final.name}\n", encoding="utf-8")
        print(final)
        print(checksum_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
