"""Release guards for version-aligned public/package metadata."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class ReleaseMetadataTest(unittest.TestCase):
    def test_current_release_version_is_consistent(self) -> None:
        runtime = _text("scripts/angemedia_gateway/version.py")
        match = re.search(r'__version__\s*=\s*"v(?P<version>\d+\.\d+\.\d+)"', runtime)
        self.assertIsNotNone(match)
        version = match.group("version")
        tag = f"v{version}"

        manifest = _text("packaging/fnos/AngeMedia/manifest")
        self.assertRegex(manifest, rf"(?m)^version\s*=\s*{re.escape(version)}\s*$")
        self.assertIn(f"API · {tag}", _text("app/www/api_docs.html"))
        self.assertIn(f"version: {tag}", _text("SKILL.md"))
        self.assertIn(f"version: {tag}", _text("skill/SKILL.md"))
        self.assertIn(f"## [{tag}]", _text("CHANGELOG.md"))
        self.assertIn(f"validated for {tag}", _text("README.md"))
        self.assertIn(f"{tag} 验证过", _text("README_CN.md"))
        self.assertIn(f"validated fnOS {tag} runtime", _text("requirements.lock"))

        for path in ("docker-compose.yml", "templates/docker-compose.yml"):
            self.assertIn(f"angemedia-gateway:{tag}-local", _text(path), path)

    def test_shipping_install_paths_use_locked_dependencies(self) -> None:
        dockerfile = _text("Dockerfile")
        ci = _text(".github/workflows/ci.yml")
        release = _text(".github/workflows/release.yml")
        docker_workflow = _text(".github/workflows/docker.yml")

        self.assertIn("COPY requirements.lock", dockerfile)
        self.assertIn("pip install -r requirements.lock", dockerfile)
        self.assertNotIn("pip install -r requirements.txt", dockerfile)
        self.assertIn("pip install -r requirements.lock", ci)
        self.assertIn("pip install -r requirements.lock", release)
        self.assertIn('"requirements.lock"', docker_workflow)
        self.assertIn('"requirements.txt"', docker_workflow)

    def test_release_workflow_builds_verified_fnos_fpk_assets(self) -> None:
        release = _text(".github/workflows/release.yml")
        self.assertIn('FNPACK_VERSION: "1.2.3"', release)
        self.assertIn(
            'FNPACK_LINUX_AMD64_SHA256: "54b97fa7b70968c4d05c79840f5daeff508957d0bb2062fdb0376d00d9615c93"',
            release,
        )
        self.assertIn("static2.fnnas.com/fnpack/fnpack-", release)
        self.assertIn("FNPACK_VERSION-linux-amd64", release)
        self.assertIn("sha256sum --check", release)
        self.assertIn("python packaging/fnos/AngeMedia/build.py", release)
        self.assertIn("--output-dir dist/fnos", release)
        self.assertIn("dist/fnos/*.fpk", release)
        self.assertIn("dist/fnos/*.fpk.sha256", release)

    def test_fnos_fresh_install_uses_local_queue_without_redis_app_dependency(self) -> None:
        manifest = _text("packaging/fnos/AngeMedia/manifest")
        install = _text("packaging/fnos/AngeMedia/cmd/install_callback")
        upgrade = _text("packaging/fnos/AngeMedia/cmd/upgrade_callback")
        self.assertRegex(manifest, r"(?m)^install_dep_apps\s*=\s*python312\s*$")
        self.assertNotRegex(manifest, r"(?m)^install_dep_apps\s*=.*\bredis\b")
        self.assertIn('write_env_value QUEUE_BACKEND "local"', install)
        self.assertIn('write_env_value QUEUE_ENABLED "true"', install)
        self.assertNotIn("REDIS_URL", install)
        self.assertNotIn("set_env_value QUEUE_BACKEND", upgrade)


if __name__ == "__main__":
    unittest.main()
