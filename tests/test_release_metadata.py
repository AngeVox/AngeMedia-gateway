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

    def test_v0212_fnos_keeps_redis_celery_dependency(self) -> None:
        manifest = _text("packaging/fnos/AngeMedia/manifest")
        install = _text("packaging/fnos/AngeMedia/cmd/install_callback")
        self.assertRegex(manifest, r"(?m)^install_dep_apps\s*=\s*redis:python312\s*$")
        self.assertIn('write_env_value QUEUE_BACKEND "celery"', install)
        self.assertIn('write_env_value QUEUE_ENABLED "true"', install)


if __name__ == "__main__":
    unittest.main()
