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

    def test_manifest_is_dual_arch_fnos_source_without_generated_checksum(self) -> None:
        text = (PKG / "manifest").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^platform\s*=\s*all\s*$")
        self.assertRegex(text, r"(?m)^install_dep_apps\s*=\s*python312\s*$")
        self.assertNotRegex(text, r"(?m)^install_dep_apps\s*=.*\bredis\b")
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
        self.assertIn('write_env_value QUEUE_BACKEND "local"', install)
        self.assertNotIn("REDIS_URL", install)
        self.assertNotIn("CELERY_BROKER_URL", install)
        self.assertNotIn("set_env_value QUEUE_BACKEND", upgrade)
        self.assertNotIn("set_env_value REDIS_URL", upgrade)
        self.assertNotIn("set_env_value CELERY_BROKER_URL", upgrade)
        self.assertIn('${QUEUE_BACKEND:-celery}', main)
        self.assertIn("local)", main)
        self.assertIn("celery)", main)
        config = (PKG / "cmd" / "config_callback").read_text(encoding="utf-8")
        self.assertIn("angemedia_gateway.cli.reset_admin", config)
        self.assertIn("ADMIN_USERNAME", config)
        self.assertIn("ADMIN_DEFAULT_PASSWORD", config)
        self.assertIn(r"%s\0%s\0", config)
        self.assertNotIn("REDIS_URL", config)
        self.assertNotIn("CELERY_BROKER_URL", config)
        install_wizard = (PKG / "wizard" / "install").read_text(encoding="utf-8")
        config_wizard = (PKG / "wizard" / "config").read_text(encoding="utf-8")
        self.assertNotIn("wizard_redis_db", install_wizard)
        self.assertNotIn("wizard_redis_db", config_wizard)
        self.assertNotIn("wizard_generate_gateway_api_key", config_wizard)
        upgrade_wizard = (PKG / "wizard" / "upgrade").read_text(encoding="utf-8")
        for forbidden in (
            "wizard_http_port",
            "wizard_redis_db",
            "wizard_admin_username",
            "wizard_admin_password",
            "wizard_generate_gateway_api_key",
        ):
            self.assertNotIn(forbidden, upgrade_wizard)

    def test_wheel_checksum_manifest_is_complete_and_unique(self) -> None:
        lines = [
            line.strip()
            for line in (PKG / "wheelhouse.SHA256SUMS").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(lines), 45)
        filenames: set[str] = set()
        for line in lines:
            digest, filename = line.split(maxsplit=1)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertTrue(filename.endswith(".whl"))
            self.assertNotIn(filename, filenames)
            filenames.add(filename)

    def test_copy_tree_filters_generated_python_cache(self) -> None:
        import importlib.util
        import tempfile

        spec = importlib.util.spec_from_file_location("angemedia_fnos_build", PKG / "build.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory(prefix="angemedia-copy-tree-") as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "keep.py").write_text("print('ok')\n", encoding="utf-8")
            (source / ".DS_Store").write_text("junk", encoding="utf-8")
            cache = source / "__pycache__"
            cache.mkdir()
            (cache / "keep.cpython-312.pyc").write_bytes(b"pyc")
            (source / "module.pyc").write_bytes(b"pyc")
            (source / "module.pyo").write_bytes(b"pyo")

            module.copy_tree(source, destination)

            self.assertTrue((destination / "keep.py").is_file())
            self.assertFalse((destination / ".DS_Store").exists())
            self.assertFalse((destination / "__pycache__").exists())
            self.assertFalse((destination / "module.pyc").exists())
            self.assertFalse((destination / "module.pyo").exists())

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
            "cmd/install_callback": "67d059aa935220c36697fd885e7f0f49b883ababa479fb7084f1ba14dd21424a",
            "cmd/upgrade_callback": "caa9b352d2ccd285f464f070f5ca44fb33ae77d0d846ae462011ca65a6e857d7",
            "cmd/config_callback": "9bdb38a7b12092f3a1fb56cc2525867f1a9cf7629f9b67f489c8d406555cb3e0",
            "cmd/main": "10365ae3b9e7d87463198a779228cbea41ad6b7474879e49b1f43a3bea501d8a",
        }
        for relative, wanted in expected.items():
            actual = hashlib.sha256((PKG / relative).read_bytes()).hexdigest()
            with self.subTest(relative=relative):
                self.assertEqual(actual, wanted)


if __name__ == "__main__":
    unittest.main()
