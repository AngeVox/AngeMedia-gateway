"""真实双层归档验证：内容缺失、替换与重复条目不能靠自洽MD5绕过。"""
from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT/'packaging/fnos/AngeMedia'
sys.path.insert(0, str(PKG))
import fnos_package as package

spec = importlib.util.spec_from_file_location('fnos_verified_build', PKG/'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class FnosPackageVerificationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fnos-package-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root/'stage'
        for name, data in {
            'manifest': b'appname = AngeMedia\nversion = 0.2.13\n',
            'cmd/main': b'#!/bin/bash\nexit 0\n', 'cmd/upgrade_callback': b'upgrade fixture',
            'i18n/en-US': b'desc=fixture', 'ICON.PNG': b'\x89PNG\r\n',
            'app/server/app/www/index.html': b'<script src="/assets/studio/app.js"></script>',
            'app/server/app/www/assets/studio/app.js': b'import "./dep.js";',
            'app/server/app/www/assets/studio/dep.js': b'export const value=1;',
            'app/server/docs/admin-session-context.md': b'context documentation',
            'app/server/vendor/wheels/fixture.whl': b'wheel fixture',
        }.items():
            path = self.stage/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.expected = package.stage_snapshot(self.stage)

    def pack(self, stage=None, checksum_override=None):
        stage = stage or self.stage
        app_bytes = io.BytesIO()
        with tarfile.open(fileobj=app_bytes, mode='w:gz') as archive:
            archive.add(stage/'app', arcname='.')
        data = app_bytes.getvalue()
        result = stage/'AngeMedia.fpk' if stage != self.stage else self.root/'fixture.fpk'
        with tarfile.open(result, 'w:gz') as archive:
            for path in sorted(stage.rglob('*')):
                if not path.is_file() or path.is_relative_to(stage/'app') or path == result:
                    continue
                body = path.read_bytes()
                if path.name == 'manifest':
                    body += ('checksum = '+(checksum_override or hashlib.md5(data).hexdigest())+'\n').encode()
                info = tarfile.TarInfo(path.relative_to(stage).as_posix())
                info.size = len(body)
                info.mode = path.stat().st_mode & 0o777
                archive.addfile(info, io.BytesIO(body))
            info = tarfile.TarInfo('app.tgz')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        return result

    def test_exact_binary_and_text_payload_with_added_checksum_passes(self):
        package.verify_package(self.pack(), 1, self.expected)

    def test_missing_entry_and_extra_module_are_rejected_despite_matching_md5(self):
        (self.stage/'app/server/app/www/index.html').unlink()
        with self.assertRaisesRegex(RuntimeError, 'payload mismatch: missing='):
            package.verify_package(self.pack(), 1, self.expected)
        (self.stage/'app/server/app/www/index.html').write_bytes(b'<script src="/assets/studio/app.js"></script>')
        (self.stage/'app/server/app/www/assets/studio/extra.js').write_bytes(b'extra')
        with self.assertRaisesRegex(RuntimeError, 'extra.js'):
            package.verify_package(self.pack(), 1, self.expected)

    def test_replaced_assets_docs_wheels_and_control_files_are_rejected(self):
        for name in ('app/server/app/www/assets/studio/dep.js', 'app/server/docs/admin-session-context.md',
                     'app/server/vendor/wheels/fixture.whl', 'cmd/main', 'cmd/upgrade_callback', 'i18n/en-US'):
            with self.subTest(name=name):
                path = self.stage/name
                original = path.read_bytes()
                path.write_bytes(b'replaced')
                with self.assertRaisesRegex(RuntimeError, 'mismatch'):
                    package.verify_package(self.pack(), 1, self.expected)
                path.write_bytes(original)

    def test_manifest_version_and_unexpected_fields_cannot_change(self):
        path = self.stage/'manifest'
        for body in (b'appname = AngeMedia\nversion = wrong\n', b'appname = AngeMedia\nversion = 0.2.13\nextra = yes\n',
                     b'appname = AngeMedia\nversion = 0.2.13\nversion = 0.2.13\n'):
            with self.subTest(body=body):
                path.write_bytes(body)
                with self.assertRaises(RuntimeError):
                    package.verify_package(self.pack(), 1, self.expected)

    def test_missing_executable_permissions_cannot_pass_matching_content(self):
        (self.stage/'cmd/main').chmod(0o644)
        expected = {**self.expected, 'executables': {**self.expected['executables'], 'cmd/main': 0o111}}
        with self.assertRaisesRegex(RuntimeError, 'executable permissions mismatch'):
            package.verify_package(self.pack(), 1, expected)

    def test_checksum_count_and_generated_file_checks_remain_enforced(self):
        archive = self.pack()
        with self.assertRaisesRegex(RuntimeError, 'wheel count'):
            package.verify_package(archive, 2, self.expected)
        changed = {**self.expected, 'manifest': {**self.expected['manifest'], 'checksum': 'not-allowed'}}
        with self.assertRaisesRegex(RuntimeError, 'manifest mismatch'):
            package.verify_package(archive, 1, changed)
        with self.assertRaisesRegex(RuntimeError, 'app checksum mismatch'):
            package.verify_package(self.pack(checksum_override='0'*32), 1, self.expected)
        with self.assertRaisesRegex(RuntimeError, 'checksum is missing'):
            package.verify_package(self.pack(checksum_override='invalid'), 1, self.expected)
        cache = self.stage/'app/server/cache.pyo'
        cache.write_bytes(b'cache')
        expected = package.stage_snapshot(self.stage)
        with self.assertRaisesRegex(RuntimeError, 'forbidden generated'):
            package.verify_package(self.pack(), 1, expected)

    def test_unsafe_archive_types_paths_and_duplicate_overwrites_are_rejected_before_extraction(self):
        for name, kind, duplicate in [('../outside', tarfile.REGTYPE, False), ('/outside', tarfile.REGTYPE, False),
                                      ('dir\\outside', tarfile.REGTYPE, False), ('link', tarfile.SYMTYPE, False),
                                      ('link', tarfile.LNKTYPE, False), ('fifo', tarfile.FIFOTYPE, False),
                                      ('same', tarfile.REGTYPE, True)]:
            with self.subTest(name=name, kind=kind):
                data = io.BytesIO()
                with tarfile.open(fileobj=data, mode='w') as archive:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    info.linkname = '../../outside'
                    archive.addfile(info)
                    if duplicate:
                        other = tarfile.TarInfo(name)
                        other.size = 8
                        archive.addfile(other, io.BytesIO(b'replaced'))
                data.seek(0)
                destination = self.root/'extract'
                destination.mkdir(exist_ok=True)
                with tarfile.open(fileobj=data) as archive, self.assertRaisesRegex(RuntimeError, 'unsafe archive'):
                    package.safe_extract(archive, destination)
                self.assertEqual(list(destination.iterdir()), [])

    def test_identical_duplicate_config_members_from_fnpack_are_allowed(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as archive:
            for _ in range(2):
                directory = tarfile.TarInfo('config')
                directory.type = tarfile.DIRTYPE
                archive.addfile(directory)
                item = tarfile.TarInfo('config/resource')
                item.size = 2
                archive.addfile(item, io.BytesIO(b'{}'))
        data.seek(0)
        with tarfile.open(fileobj=data) as archive:
            package.safe_extract(archive, self.root/'fnpack-config')
        self.assertEqual((self.root/'fnpack-config/config/resource').read_bytes(), b'{}')

    def test_staging_copies_complete_runtime_and_frontend_from_repository(self):
        stage = self.root/'actual-stage'
        stage.mkdir()
        build.build_stage(ROOT, PKG, stage)
        for folder in ('app', 'scripts', 'docs'):
            expected = {p.relative_to(ROOT/folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                        and p.suffix not in ('.pyc', '.pyo') and p.name != '.DS_Store'}
            self.assertEqual(package.file_hashes(stage/'app/server'/folder), expected)
        self.assertEqual((stage/'cmd/main').read_bytes(), (PKG/'cmd/main').read_bytes())

    def test_failed_final_verification_preserves_existing_output_and_checksum(self):
        output = self.root/'output'
        output.mkdir()
        final = output/'AngeMedia-v0.2.13-fnOS-all.fpk'
        checksum = final.with_suffix('.fpk.sha256')
        final.write_bytes(b'previous verified package')
        checksum.write_bytes(b'previous checksum')

        def stage_files(repo, pkg, stage):
            shutil.copytree(self.stage, stage, dirs_exist_ok=True)

        def pack_with_missing_resource(command, *, cwd):
            (cwd/'app/server/app/www/index.html').unlink()
            self.pack(cwd)

        with patch.object(sys, 'argv', ['build.py', '--output-dir', str(output), '--wheelhouse', str(self.root)]), \
                patch.object(build.shutil, 'which', return_value='/fixture/fnpack'), \
                patch.object(build, 'parse_expected_wheels', return_value={'fixture.whl': 'fixture'}), \
                patch.object(build, 'build_stage', side_effect=stage_files), \
                patch.object(build, 'prepare_wheelhouse'), patch.object(build, 'run', side_effect=pack_with_missing_resource):
            with self.assertRaisesRegex(RuntimeError, 'payload mismatch'):
                build.main()
        self.assertEqual(final.read_bytes(), b'previous verified package')
        self.assertEqual(checksum.read_bytes(), b'previous checksum')
