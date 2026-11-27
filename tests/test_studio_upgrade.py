"""前端内容快照、模块版本和旧Cookie协议的真实HTTP边界。"""
import os
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_admin_session_context as context_tests
from angemedia_gateway.server import RevalidatingStaticFiles
from angemedia_gateway.services.studio_assets import StudioAssets, studio_assets


class StudioAssetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='studio-assets-')
        self.root = Path(self.temp.name)
        (self.root/'assets/studio').mkdir(parents=True)
        (self.root/'index.html').write_bytes(b'<script type="module" src="/assets/studio/app.js"></script>')
        (self.root/'assets/studio/app.js').write_bytes(b'import "./dep.js";')
        (self.root/'assets/studio/dep.js').write_bytes(b'export const version="A";')

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        return StudioAssets(self.root/'assets', self.root/'index.html')

    def client(self, snapshot):
        app = FastAPI();app.mount('/assets', RevalidatingStaticFiles(directory=self.root/'assets', snapshot=snapshot))
        return TestClient(app)

    def test_html_and_relative_imports_share_content_namespace(self):
        snapshot = self.snapshot()
        self.assertIn('/assets/'+snapshot.revision+'/studio/app.js', snapshot.page().body.decode())
        with self.client(snapshot) as client:
            response = client.get('/assets/'+snapshot.revision+'/studio/app.js')
            self.assertIn('immutable', response.headers['cache-control'])
            self.assertIn('./dep.js', response.text)
            self.assertEqual(client.get('/assets/'+snapshot.revision+'/studio/dep.js').status_code, 200)
        self.assertEqual(snapshot.page().headers['cache-control'], 'no-store')

    def test_process_snapshot_never_reads_mutated_disk_under_old_url(self):
        first = self.snapshot();path=self.root/'assets/studio/dep.js';stat=path.stat()
        path.write_bytes(b'export const version="B";');os.utime(path, ns=(stat.st_atime_ns,stat.st_mtime_ns))
        second = self.snapshot();self.assertNotEqual(first.revision,second.revision)
        with self.client(first) as a,self.client(second) as b:
            old=a.get('/assets/studio/dep.js');new=b.get('/assets/studio/dep.js',headers={'If-None-Match':old.headers['etag']})
            self.assertIn('"A"',old.text);self.assertEqual(new.status_code,200);self.assertIn('"B"',new.text)
            self.assertNotEqual(old.headers['etag'],new.headers['etag'])

    def test_old_entry_redirects_whole_graph_but_old_dependency_is_rejected(self):
        first=self.snapshot();(self.root/'assets/studio/dep.js').write_bytes(b'new deployment');second=self.snapshot()
        with self.client(second) as client:
            entry=client.get('/assets/'+first.revision+'/studio/app.js',follow_redirects=False)
            self.assertEqual(entry.status_code,307)
            self.assertEqual(entry.headers['location'],'/assets/'+second.revision+'/studio/app.js')
            dependency=client.get('/assets/'+first.revision+'/studio/dep.js')
            self.assertEqual(dependency.status_code,409)
            self.assertEqual(dependency.headers['cache-control'],'no-store')
            self.assertNotIn(b'new deployment',dependency.content)

    def test_etag_head_missing_and_method_contract(self):
        with self.client(self.snapshot()) as client:
            first=client.get('/assets/studio/app.js')
            self.assertEqual(first.headers['cache-control'],'no-cache')
            for candidate in (first.headers['etag'],'W/'+first.headers['etag'],'*'):
                self.assertEqual(client.get('/assets/studio/app.js',headers={'If-None-Match':candidate}).status_code,304)
            head=client.head('/assets/studio/app.js');self.assertEqual(head.content,b'')
            self.assertEqual(int(head.headers['content-length']),len(first.content))
            self.assertEqual(client.post('/assets/studio/app.js').status_code,405)
            self.assertEqual(client.get('/assets/not-found').status_code,404)

    def test_index_only_change_changes_revision(self):
        first=self.snapshot();(self.root/'index.html').write_bytes(b'new HTML entry')
        self.assertNotEqual(first.revision,self.snapshot().revision)

    def test_outside_symlink_is_not_snapshotted(self):
        secret=self.root/'outside.txt';secret.write_bytes(b'not-public')
        try:(self.root/'assets/leak.txt').symlink_to(secret)
        except OSError as error:self.skipTest('Platform does not allow fixture symlink: '+str(error))
        snapshot=self.snapshot();self.assertNotIn('leak.txt',snapshot.files)
        with self.client(snapshot) as client:self.assertEqual(client.get('/assets/leak.txt').status_code,404)


class StudioProtocolTest(unittest.TestCase):
    def setUp(self):
        self.fixture=context_tests.AdminSessionContextTest();self.fixture.setUp()
        self.client=self.fixture.client;self.context=self.fixture.login()

    def tearDown(self):
        self.fixture.tearDown()

    def test_contextless_old_cookie_reads_writes_uploads_logout_and_stream_are_rejected(self):
        calls=[lambda:self.client.get('/v1/admin/me'),
               lambda:self.client.patch('/v1/admin/account',json={'current_password':'wrong','new_username':'must-not-change'}),
               lambda:self.client.post('/v1/uploads',files={'files':('fixture.png',b'fixture','image/png')}),
               lambda:self.client.post('/v1/admin/logout'),
               lambda:self.client.get('/v1/admin/jobs/missing/stream')]
        for call in calls:
            response=call();self.assertEqual(response.status_code,409)
            self.assertEqual(response.json()['detail']['code'],'session_context_required')
        self.assertTrue(self.client.get('/v1/admin/session').json()['authenticated'])

    def test_wrong_revision_is_rejected_but_cookie_sdk_context_does_not_require_asset_version(self):
        headers={'X-Studio-Session-Context':self.context}
        self.assertEqual(self.client.get('/v1/admin/me',headers=headers).status_code,200)
        response=self.client.get('/v1/admin/me',headers={**headers,'X-Studio-Revision':'r-stale'})
        self.assertEqual(response.status_code,409);self.assertEqual(response.json()['detail']['code'],'studio_upgrade_required')
        self.assertEqual(self.client.get('/v1/admin/session',headers={'X-Studio-Revision':'r-stale'}).status_code,409)
        self.assertEqual(self.client.post('/v1/admin/login',headers={'X-Studio-Revision':'r-stale'},json={'username':'fixture-a','password':'fixture-password'}).status_code,409)
        response=self.client.get('/v1/admin/me',headers={**headers,'X-Studio-Revision':studio_assets().revision})
        self.assertEqual(response.status_code,200)

    def test_stale_revision_cannot_logout_or_start_stream(self):
        headers={'X-Studio-Session-Context':self.context,'X-Studio-Revision':'r-stale'}
        self.assertEqual(self.client.post('/v1/admin/logout',headers=headers).status_code,409)
        response=self.client.get('/v1/admin/jobs/missing/stream',params={'studio_session_context':self.context,'studio_revision':'r-stale'})
        self.assertEqual(response.status_code,409);self.assertEqual(response.json()['detail']['code'],'studio_upgrade_required')
        self.assertTrue(self.client.get('/v1/admin/session').json()['authenticated'])
