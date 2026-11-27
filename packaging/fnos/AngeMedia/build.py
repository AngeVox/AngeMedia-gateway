#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 独立脚本与测试按文件加载时共用同一轮子模块。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnos_wheels import download_wheels, parse_expected_wheels, sha256, verify_wheelhouse
from fnos_package import stage_snapshot, verify_package


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store"),
    )


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
            download_wheels(python_bin, repo_root / "requirements.lock", download_dir)
            source = download_dir
        else:
            source = wheelhouse_source.resolve()

        verify_wheelhouse(source, expected, repo_root / "requirements.lock")

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
    # 不依赖源码所在文件系统是否保留Git执行位，生命周期入口必须可直接执行。
    for path in (stage / "cmd").iterdir():
        if path.is_file():
            path.chmod(0o755)

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the AngeMedia fnOS/FYGO dual-architecture FPK package")
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
        expected = stage_snapshot(stage)
        run(["fnpack", "build"], cwd=stage)
        built = stage / "AngeMedia.fpk"
        final = output_dir / f"AngeMedia-v{version}-fnOS-all.fpk"
        # 先验暂存产物再输出，失败不能覆盖既有已验包或遗留成功校验文件。
        verify_package(built, len(expected_wheels), expected)
        shutil.copy2(built, final)
        checksum_file = final.with_suffix(final.suffix + ".sha256")
        checksum_file.write_text(f"{sha256(final)}  {final.name}\n", encoding="utf-8")
        print(final)
        print(checksum_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
