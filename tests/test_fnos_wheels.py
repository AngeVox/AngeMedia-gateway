"""离线轮子闭包与供应目标验收；合成真实wheel元数据，不依赖外部网络。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fnos_wheel_contracts", ROOT / "packaging/fnos/AngeMedia/fnos_wheels.py")
wheels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wheels)


class FnosWheelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fnos-wheels-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = self.root / "requirements.lock"
        self.lock.write_text("alpha==1.0\nbeta==2.0\n", encoding="utf-8")

    def wheel(self, name, version, *, tag="py3-none-any", dependencies=(), python=">=3.11"):
        filename = f"{name}-{version}-{tag}.whl"
        metadata = f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\nRequires-Python: {python}\n"
        metadata += "".join(f"Requires-Dist: {value}\n" for value in dependencies)
        with zipfile.ZipFile(self.root / filename, "w") as archive:
            archive.writestr(f"{name}-{version}.dist-info/METADATA", metadata)
        return filename

    def expected(self):
        return {file.name:wheels.sha256(file) for file in self.root.glob("*.whl")}

    def valid(self):
        self.wheel("alpha", "1.0", dependencies=("beta>=2",))
        self.wheel("beta", "2.0")

    def test_complete_shared_wheels_cover_both_targets(self):
        self.valid()
        self.assertEqual(wheels.verify_wheelhouse(self.root, self.expected(), self.lock), {"x86_64":2,"aarch64":2})

    def test_missing_arm_wheel_and_wrong_python_are_rejected(self):
        self.wheel("alpha", "1.0", tag="cp312-cp312-manylinux_2_28_x86_64")
        self.wheel("beta", "2.0")
        with self.assertRaisesRegex(RuntimeError, "aarch64"):
            wheels.verify_wheelhouse(self.root, self.expected(), self.lock)
        self.wheel("alpha", "1.0", tag="cp312-cp312-manylinux_2_28_aarch64", python=">=3.13")
        with self.assertRaisesRegex(RuntimeError, "Python"):
            wheels.verify_wheelhouse(self.root, self.expected(), self.lock)

    def test_incomplete_and_incompatible_dependency_closure_are_rejected(self):
        self.wheel("alpha", "1.0", dependencies=("missing>=1",))
        self.wheel("beta", "2.0")
        with self.assertRaisesRegex(RuntimeError, "闭包"):
            wheels.verify_wheelhouse(self.root, self.expected(), self.lock)
        self.wheel("alpha", "1.0", dependencies=("beta>=3",))
        with self.assertRaisesRegex(RuntimeError, "闭包"):
            wheels.verify_wheelhouse(self.root, self.expected(), self.lock)

    def test_target_markers_and_transitive_extras_are_evaluated(self):
        self.wheel("alpha", "1.0", dependencies=('windows-only; sys_platform == "win32"', 'beta[feature]>=2'))
        self.wheel("beta", "2.0", dependencies=('missing; extra == "feature"',))
        with self.assertRaisesRegex(RuntimeError, "missing"):
            wheels.verify_wheelhouse(self.root, self.expected(), self.lock)
        self.wheel("beta", "2.0", dependencies=('missing; implementation_version == "3.12.0"',))
        with self.assertRaisesRegex(RuntimeError, "missing"):
            wheels.verify_wheelhouse(self.root, self.expected(), self.lock)
        self.wheel("beta", "2.0", dependencies=('missing; python_version < "3.12"',))
        self.assertEqual(wheels.verify_wheelhouse(self.root, self.expected(), self.lock)["aarch64"], 2)

    def test_tampered_bytes_extra_wheels_and_metadata_are_rejected(self):
        self.valid();expected = self.expected()
        with (self.root / next(iter(expected))).open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "哈希"):
            wheels.verify_wheelhouse(self.root, expected, self.lock)
        self.valid();self.wheel("extra", "1.0")
        with self.assertRaisesRegex(RuntimeError, "集合"):
            wheels.verify_wheelhouse(self.root, expected, self.lock)

    def test_checksum_manifest_cannot_escape_directory_or_hide_duplicates(self):
        path = self.root / "SHA256SUMS"
        for content in ("", "bad alpha.whl", "0"*64+"  ../escape.whl", ("0"*64+"  alpha.whl\n")*2):
            path.write_text(content, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                wheels.parse_expected_wheels(path)

    def test_download_resolves_full_lock_for_each_architecture(self):
        with patch.object(wheels.subprocess, "run") as run:
            wheels.download_wheels("python", self.lock, self.root / "downloads")
        self.assertEqual(run.call_count, 2)
        for architecture, call in zip(wheels.ARCHITECTURES, run.call_args_list):
            command = call.args[0]
            self.assertIn(f"manylinux_2_28_{architecture}", command)
            self.assertIn(f"manylinux2014_{architecture}", command)
            self.assertIn(str(self.lock), command)
            self.assertNotIn("--no-deps", command)


if __name__ == "__main__":
    unittest.main()
