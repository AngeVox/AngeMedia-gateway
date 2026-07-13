"""Static contracts for the fnOS/FYGO native package source."""
from __future__ import annotations

import configparser
import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging" / "fnos" / "AngeMedia"


class FnosPackagingContractTest(unittest.TestCase):
    def test_required_package_sources_exist(self) -> None:
        required = (
            "manifest",
            "build.py",
            "wheelhouse.SHA256SUMS",
            "assets/ICON.PNG",
            "assets/ICON_256.PNG",
            "app/ui/config",
            "cmd/main",
            "cmd/install_callback",
            "cmd/config_callback",
            "cmd/upgrade_callback",
            "cmd/uninstall_callback",
            "config/privilege",
            "config/resource",
            "wizard/install",
            "wizard/config",
            "wizard/upgrade",
            "wizard/uninstall",
            "i18n/en-US",
            "i18n/zh-CN",
        )
        for relative in required:
            with self.subTest(relative=relative):
                self.assertTrue((PKG / relative).is_file())

    def test_manifest_is_x86_fnos_source_without_generated_checksum(self) -> None:
        text = (PKG / "manifest").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^platform\s*=\s*x86\s*$")
        self.assertRegex(text, r"(?m)^install_dep_apps\s*=\s*redis:python312\s*$")
        self.assertRegex(text, r"(?m)^desc\s*=\s*\$\{common\.desc\}\s*$")
        self.assertRegex(text, r"(?m)^changelog\s*=\s*\$\{common\.changelog\}\s*$")
        self.assertNotRegex(text, r"(?m)^checksum\s*=")

    def test_wizard_json_and_locale_placeholder_contract(self) -> None:
        wizard_files = tuple(PKG.joinpath("wizard", name) for name in ("install", "config", "upgrade", "uninstall"))
        for path in wizard_files:
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))

        locale_keys: dict[str, set[tuple[str, str]]] = {}
        for locale in ("en-US", "zh-CN"):
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read(PKG / "i18n" / locale, encoding="utf-8")
            locale_keys[locale] = {
                (section, key)
                for section in parser.sections()
                for key in parser[section]
            }
        self.assertEqual(locale_keys["en-US"], locale_keys["zh-CN"])

        placeholder = re.compile(r"\$\{([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\}")
        referenced: set[tuple[str, str]] = set()
        for path in (PKG / "manifest", *wizard_files):
            referenced.update(placeholder.findall(path.read_text(encoding="utf-8")))
        self.assertTrue(referenced)
        self.assertEqual(referenced, locale_keys["en-US"])

    def test_package_copy_is_policy_neutral(self) -> None:
        forbidden = (
            "fnOS",
            "50 张/天",
            "400 张/天",
            "50 free images",
            "400 free images",
            "当前可免费调用",
            "currently free to call",
            "&mdash;",
        )
        for locale in ("en-US", "zh-CN"):
            text = (PKG / "i18n" / locale).read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(locale=locale, token=token):
                    self.assertNotIn(token, text)

    def test_shared_resource_names_are_namespaced(self) -> None:
        resource = json.loads((PKG / "config" / "resource").read_text(encoding="utf-8"))
        names = [item["name"] for item in resource["data-share"]["shares"]]
        self.assertEqual(
            names,
            ["angemedia/generated", "angemedia/uploads", "angemedia/logs"],
        )

    def test_lifecycle_scripts_are_offline_and_share_aware(self) -> None:
        install = (PKG / "cmd" / "install_callback").read_text(encoding="utf-8")
        upgrade = (PKG / "cmd" / "upgrade_callback").read_text(encoding="utf-8")
        main = (PKG / "cmd" / "main").read_text(encoding="utf-8")
        for source in (install, upgrade):
            for required in (
                "TRIM_DATA_SHARE_PATHS",
                "resolve_share_path",
                "--no-index",
                "--only-binary=:all:",
                "--find-links",
                "sha256sum -c SHA256SUMS",
            ):
                with self.subTest(required=required):
                    self.assertIn(required, source)
            self.assertNotIn("wizard_pip_index", source)
            self.assertNotIn("requirements.txt", source)
        self.assertIn("ANGEMEDIA_LOG_DIR", install)
        self.assertIn("ANGEMEDIA_LOG_DIR", upgrade)
        self.assertIn("ANGEMEDIA_LOG_DIR", main)

    def test_wheel_checksum_manifest_is_complete_and_unique(self) -> None:
        lines = [
            line.strip()
            for line in (PKG / "wheelhouse.SHA256SUMS").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(lines), 39)
        filenames: set[str] = set()
        for line in lines:
            digest, filename = line.split(maxsplit=1)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertTrue(filename.endswith(".whl"))
            self.assertNotIn(filename, filenames)
            filenames.add(filename)

    def test_build_script_stages_core_and_verifies_output(self) -> None:
        source = (PKG / "build.py").read_text(encoding="utf-8")
        for required in (
            'for directory in ("app", "scripts", "docs")',
            '"requirements.lock"',
            '"wheelhouse.SHA256SUMS"',
            '["fnpack", "build"]',
            "verify_package(final",
            "packed app checksum mismatch",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

    def test_committed_package_files_match_reviewed_hashes(self) -> None:
        expected = {
            "cmd/install_callback": "cc0d531da8ae73eb96232a79f2e6d84c70b549f598611c7019eb615d25a965d4",
            "cmd/upgrade_callback": "caa9b352d2ccd285f464f070f5ca44fb33ae77d0d846ae462011ca65a6e857d7",
            "cmd/config_callback": "90030ee40c3d9c283921006dacdfa43f45883c0c7ea07be017e2c5d420791254",
            "cmd/main": "2063495fdd1c7e64f911abf80979c27697399b229f3a8ac5596fbd2d44a835d5",
        }
        for relative, wanted in expected.items():
            actual = hashlib.sha256((PKG / relative).read_bytes()).hexdigest()
            with self.subTest(relative=relative):
                self.assertEqual(actual, wanted)


if __name__ == "__main__":
    unittest.main()
