from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTest(unittest.TestCase):
    def test_env_example_uses_runtime_variable_names(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for forbidden in ["POLLINATIONS_ENABLED", "AGNES_AI_API_KEY", "ADMIN_API_KEY"]:
            self.assertNotRegex(text, rf"(?m)^{re.escape(forbidden)}=")

        required_keys = [
            "SILICONFLOW_API_KEY",
            "MODELSCOPE_API_KEY",
            "POLLINATIONS_API_KEY",
            "AGNES_API_KEY",
            "OPENAI_IMAGE_API_KEY",
            "OPENAI_IMAGE_BASE_URL",
            "OPENAI_IMAGE_MODEL",
            "IMAGE_PROVIDER_TIMEOUT",
            "VIDEO_PROVIDER_TIMEOUT",
            "GATEWAY_API_KEY",
            "ANGE_LLM_API_KEY",
            "ANGE_LLM_BASE_URL",
            "ANGE_LLM_MODEL",
            "BUILTIN_PROVIDER_POLLINATIONS_ENABLED",
            "BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED",
        ]
        for key in required_keys:
            with self.subTest(key=key):
                self.assertRegex(text, rf"(?m)^{re.escape(key)}=")
        self.assertNotRegex(text, r"(?m)^AGNES_VIDEO_SUBMIT_TIMEOUT=")
        self.assertNotRegex(text, r"(?m)^ANGE_LLM_MODEL=gpt-4o-mini$")

    def test_release_workflow_excludes_runtime_and_development_files(self) -> None:
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for pattern in [
            "--exclude='.env'",
            "--exclude='generated'",
            "--exclude='uploads'",
            "--exclude='output'",
            "--exclude='DEVELOPMENT.md'",
            "--exclude='AUDIT*.md'",
            "--exclude='HANDOFF*.md'",
            "--exclude='*Release-Report*.md'",
        ]:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, text)

    def test_public_repository_has_no_development_handoff_docs(self) -> None:
        forbidden = []
        for path in ROOT.glob("*.md"):
            name = path.name
            if (
                name == "DEVELOPMENT.md"
                or name.startswith("AUDIT")
                or name.startswith("HANDOFF")
                or "Release-Report" in name
            ):
                forbidden.append(name)
        self.assertEqual(forbidden, [])

    def test_public_docs_do_not_reference_removed_entrypoints(self) -> None:
        docs = [
            ROOT / "README.md",
            ROOT / "README_CN.md",
            ROOT / "SKILL.md",
            ROOT / "skill" / "SKILL.md",
            *list((ROOT / "docs").glob("*.md")),
            *list((ROOT / "skill" / "docs").glob("*.md")),
        ]
        forbidden = ["scripts/image-gateway", "image-gateway/gateway.py", "`gateway.py`"]
        for path in docs:
            text = path.read_text(encoding="utf-8")
            for value in forbidden:
                with self.subTest(path=path.name, value=value):
                    self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
