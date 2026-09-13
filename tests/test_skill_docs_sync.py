"""Release guard: bundled Agent Skill docs must mirror the canonical docs tree."""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "docs"
BUNDLED = ROOT / "skill" / "docs"


def _files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class SkillDocsSyncTest(unittest.TestCase):
    def test_bundled_skill_docs_exactly_match_canonical_docs(self) -> None:
        self.assertEqual(_files(BUNDLED), _files(CANONICAL))


if __name__ == "__main__":
    unittest.main()
