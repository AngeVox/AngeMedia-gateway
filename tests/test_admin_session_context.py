"""Cookie与页面会话标记不一致时，业务入口必须先拒绝。"""
import asyncio
import os
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_admin_api import C, app, ensure_default_admin_user, init_db
from fastapi.testclient import TestClient
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.helpers import now_iso
from angemedia_gateway.media_file_response import MediaFileResponse
from angemedia_gateway.repositories.gateway_keys import create_gateway_api_key
from angemedia_gateway.security import admin_session_context, hash_password, hash_token
from angemedia_gateway.services.studio_assets import studio_assets


class AdminSessionContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='studio-context-')
        self.db = patch.object(C, 'DB_FILE', Path(self.temp.name)/'test.db')
        self.db.start()
        self.env = patch.dict(os.environ, ADMIN_USERNAME='fixture-a', ADMIN_DEFAULT_PASSWORD='fixture-password')
        self.env.start()
        init_db(); ensure_default_admin_user()
        with closing(db_connect()) as conn, conn:
            conn.execute('INSERT INTO admin_users(username,password_hash,created_at,updated_at) VALUES(?,?,?,?)',
                         ('fixture-b', hash_password('fixture-password'), now_iso(), now_iso()))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.env.stop(); self.db.stop(); self.temp.cleanup()

    def login(self, username='fixture-a'):
        response = self.client.post('/v1/admin/login', json={'username': username, 'password': 'fixture-password'})
        self.assertEqual(response.status_code, 200, response.text)
        self.context = response.json()['session_context']
        return self.context

    def rejected(self, response):
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'session_context_changed')
        self.assertNotIn('set-cookie', response.headers)

    def test_context_is_domain_separated_noncredential_and_session_read_matches(self):
        context = self.login(); token = self.client.cookies.get('am_admin_session')
        self.assertEqual(len(context), 64); self.assertNotEqual(context, token); self.assertNotEqual(context, hash_token(token))
        self.assertEqual(context, admin_session_context(token))
        self.assertEqual(self.client.get('/v1/admin/session').json()['session_context'], context)
        self.assertEqual(self.client.get('/v1/admin/me', headers={'X-Studio-Session-Context': context}).status_code, 200)

    def test_old_context_same_account_new_login_is_rejected_without_changing_cookie(self):
        old = self.login(); current = self.login(); self.assertNotEqual(old, current)
        self.rejected(self.client.get('/v1/admin/me', headers={'X-Studio-Session-Context': old}))
        self.assertEqual(self.client.get('/v1/admin/session').json()['session_context'], current)

    def test_different_account_write_never_reaches_account_mutation(self):
        old = self.login(); self.login('fixture-b')
        with patch('angemedia_gateway.routes.admin_auth.update_admin_account') as mutation:
            self.rejected(self.client.patch('/v1/admin/account', headers={'X-Studio-Session-Context': old},
                json={'current_password': 'fixture-password', 'new_username': 'must-not-change'}))
            mutation.assert_not_called()
        self.assertEqual(self.client.get('/v1/admin/me', headers={'X-Studio-Session-Context': self.context}).json()['username'], 'fixture-b')

    def test_old_context_upload_is_rejected_before_storage(self):
        old = self.login(); self.login('fixture-b')
        with patch.object(C, 'UPLOAD_DIR', Path(self.temp.name)/'uploads'):
            self.rejected(self.client.post('/v1/uploads', headers={'X-Studio-Session-Context': old},
                files={'files': ('fixture.png', b'fixture', 'image/png')}))
            self.assertFalse((Path(self.temp.name)/'uploads').exists())

    def test_old_logout_cannot_revoke_new_cookie_but_current_logout_can(self):
        old = self.login(); current = self.login('fixture-b')
        self.rejected(self.client.post('/v1/admin/logout', headers={'X-Studio-Session-Context': old}))
        self.assertEqual(self.client.get('/v1/admin/session').json()['session_context'], current)
        self.assertTrue(self.client.post('/v1/admin/logout', headers={'X-Studio-Session-Context': current}).json()['ok'])
        self.assertFalse(self.client.get('/v1/admin/session').json()['authenticated'])

    def test_stream_query_is_checked_before_job_lookup_and_conflicts_are_rejected(self):
        old = self.login(); current = self.login('fixture-b')
        self.rejected(self.client.get('/v1/admin/jobs/missing/stream', params={'studio_session_context': old}))
        self.rejected(self.client.get('/v1/admin/me', params={'studio_session_context': old}, headers={'X-Studio-Session-Context': current}))
        self.assertEqual(self.client.get('/v1/admin/me', params={'studio_session_context': current}).status_code, 200)

    def test_malformed_context_never_raises_internal_error(self):
        self.login()
        for value in ('', 'x', 'a'*65, '界'*64):
            with self.subTest(value=value):
                self.rejected(self.client.get('/v1/admin/me', params={'studio_session_context': value}))

    def test_context_alone_never_authenticates(self):
        context = self.login(); self.client.cookies.clear()
        self.assertEqual(self.client.get('/v1/admin/me', headers={'X-Studio-Session-Context': context}).status_code, 401)
        self.assertTrue(self.client.post('/v1/admin/logout', headers={'X-Studio-Session-Context': context}).json()['ok'])

    def test_contextless_cookie_clients_must_update_and_explicit_clients_are_independent(self):
        self.login(); independent = TestClient(app)
        try:
            reply = independent.post('/v1/admin/login', json={'username': 'fixture-b', 'password': 'fixture-password'})
            self.assertEqual(reply.status_code, 200)
            for client in (self.client, independent):
                response = client.get('/v1/admin/me')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['detail']['code'], 'session_context_required')
            self.assertEqual(self.client.get('/v1/admin/me', headers={'X-Studio-Session-Context': self.context}).json()['username'], 'fixture-a')
            self.assertEqual(independent.get('/v1/admin/me', headers={'X-Studio-Session-Context': reply.json()['session_context']}).json()['username'], 'fixture-b')
        finally:
            independent.close()

    def test_native_media_preserves_ranges_without_loosening_session_binding(self):
        output = Path(self.temp.name) / 'generated'
        output.mkdir()
        payload = bytes(range(20))
        (output / 'fixture.mp4').write_bytes(payload)
        old = self.login()
        current = self.login('fixture-b')
        path = '/v1/admin/media/generated/fixture.mp4'
        query = {'studio_session_context': current, 'studio_revision': studio_assets().revision}
        with patch.object(C, 'OUTPUT_DIR', output):
            self.assertEqual(self.client.get(path).json()['detail']['code'], 'session_context_required')
            self.rejected(self.client.get(path, params={'studio_session_context': old}, headers={'Range': 'bytes=4-7'}))
            obsolete = self.client.get(path, params={**query, 'studio_revision': 'r-obsolete'})
            self.assertEqual(obsolete.json()['detail']['code'], 'studio_upgrade_required')
            head = self.client.head(path, params=query)
            self.assertEqual((head.status_code, head.content, head.headers['content-length']), (200, b'', '20'))
            self.assertEqual(head.headers['cache-control'], 'private, no-store')
            self.assertEqual(head.headers['referrer-policy'], 'no-referrer')
            partial = self.client.get(path, params=query, headers={'Range': 'bytes=4-7'})
            self.assertEqual((partial.status_code, partial.content), (206, payload[4:8]))
            self.assertEqual(partial.headers['content-range'], 'bytes 4-7/20')
            unsatisfiable = self.client.get(path, params=query, headers={'Range': 'bytes=30-31'})
            self.assertEqual(unsatisfiable.status_code, 416)
            self.client.cookies.clear()
            self.assertEqual(self.client.get(path, params=query).status_code, 401)
            self.assertEqual(self.client.head(path, params=query).status_code, 401)

    @unittest.skipUnless(os.name == 'posix', 'Windows 持有句柄期间会阻止重命名')
    def test_media_response_keeps_the_open_file_after_path_changes(self):
        root = Path(self.temp.name).resolve()
        path = root / 'video.mp4'
        path.write_bytes(b'original')
        replacement = root / 'next.mp4'
        replacement.write_bytes(b'other')
        messages = []

        async def send(message):
            if message['type'] == 'http.response.start':
                replacement.replace(path)
            messages.append(message)

        scope = {'type': 'http', 'method': 'GET', 'headers': [(b'range', b'bytes=1-3')],
                 'extensions': {'http.response.pathsend': {}}}
        asyncio.run(MediaFileResponse(root, ('video.mp4',))(scope, None, send))
        self.assertEqual(messages[0]['status'], 206)
        self.assertEqual(b''.join(item['body'] for item in messages[1:]), b'rig')

    @unittest.skipUnless(os.name == 'posix', '需要本地符号链接测试夹具')
    def test_native_media_rejects_symbolic_links_in_media_storage(self):
        output = Path(self.temp.name) / 'generated'
        output.mkdir()
        real = output / 'original.mp4'
        real.write_bytes(b'fixture media')
        (output / 'shortcut.mp4').symlink_to(real)
        query = {'studio_session_context': self.login(), 'studio_revision': studio_assets().revision}
        with patch.object(C, 'OUTPUT_DIR', output):
            success = self.client.get('/v1/admin/media/generated/original.mp4', params=query)
            self.assertEqual((success.status_code, success.content), (200, b'fixture media'))
            self.assertEqual(self.client.get('/v1/admin/media/generated/shortcut.mp4', params=query).status_code, 404)

    def test_openapi_media_operations_have_stable_names_and_response_shapes(self):
        paths = app.openapi()['paths']
        operation_ids = [
            operation['operationId']
            for methods in paths.values()
            for operation in methods.values()
            if isinstance(operation, dict) and 'operationId' in operation
        ]
        self.assertEqual(len(operation_ids), len(set(operation_ids)))
        expected = {
            '/generated/{filename}': 'serve_generated_file_generated__filename',
            '/uploads/{filename}': 'serve_upload_file_uploads__filename',
            '/v1/admin/media/{area}/{filename}': 'serve_studio_media_v1_admin_media__area___filename',
        }
        for path, prefix in expected.items():
            for method in ('get', 'head'):
                with self.subTest(path=path, method=method):
                    operation = paths[path][method]
                    self.assertEqual(operation['operationId'], f'{prefix}__{method}')
                    responses = operation['responses']
                    self.assertIn('416', responses)
                    for status in ('200', '206'):
                        if method == 'get':
                            self.assertIn('application/octet-stream', responses[status]['content'])
                        else:
                            self.assertNotIn('content', responses[status])

    def test_media_405_lists_get_and_head(self):
        for path in (
            '/generated/fixture.mp4',
            '/uploads/fixture.png',
            '/v1/admin/media/generated/fixture.mp4',
        ):
            with self.subTest(path=path):
                response = self.client.post(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(set(response.headers['allow'].split(', ')), {'GET', 'HEAD'})

    def test_generated_and_uploaded_files_keep_public_auth_contract_for_get_and_head(self):
        root = Path(self.temp.name)
        output, uploads = root / 'generated', root / 'uploads'
        content = b'fixture file bytes'
        for folder in (output, uploads):
            folder.mkdir()
            (folder / 'fixture.png').write_bytes(content)
        context = self.login()
        key = create_gateway_api_key(name='media-file-boundary')['key']
        with patch.multiple(C, OUTPUT_DIR=output, UPLOAD_DIR=uploads), closing(TestClient(app)) as public:
            for path in ('/generated/fixture.png', '/uploads/fixture.png'):
                for method, body in (('GET', content), ('HEAD', b'')):
                    with self.subTest(path=path, method=method):
                        self.assertEqual(public.request(method, path).status_code, 401)
                        self.assertEqual(self.client.request(method, path).status_code, 409)
                        gateway = public.request(method, path, headers={'X-API-Key': key})
                        session = self.client.request(method, path, headers={'X-Studio-Session-Context': context})
                        for response in (gateway, session):
                            self.assertEqual((response.status_code, response.content), (200, body))
                            self.assertEqual(response.headers['content-length'], str(len(content)))

    def test_native_media_uses_existing_layer_gate_and_downloads_active_formats(self):
        self.login()
        query = {'studio_session_context': self.context}
        root = Path(self.temp.name)
        output = root / 'generated'
        uploads = root / 'uploads'
        layer_path = output / '.image-layers' / ('b' * 32) / '000.png'
        layer_path.parent.mkdir(parents=True)
        layer_path.write_bytes(b'private fixture')
        uploads.mkdir()
        static_svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
                      b'<rect width="100" height="100" fill="#128080"/></svg>')
        (uploads / 'fixture.svg').write_bytes(static_svg)
        (output / 'fixture.svg').write_bytes(static_svg)
        (uploads / 'fixture.png').write_bytes(b'public fixture')
        with patch.multiple(C, OUTPUT_DIR=output, UPLOAD_DIR=uploads):
            layer = self.client.get(f'/v1/admin/media/generated/{layer_path.relative_to(output).as_posix()}', params=query)
            self.assertEqual(layer.status_code, 404)
            image = self.client.get('/v1/admin/media/uploads/fixture.png', params=query)
            self.assertEqual((image.status_code, image.headers['content-type']), (200, 'image/png'))
            self.assertEqual(image.headers['content-disposition'], 'inline')
            self.assertEqual(self.client.get('/v1/admin/media/uploads/fixture.svg', params=query).status_code, 404)
            svg = self.client.get('/v1/admin/media/generated/fixture.svg', params=query)
            self.assertEqual((svg.status_code, svg.content), (200, static_svg))
            self.assertEqual(svg.headers['content-type'], 'application/octet-stream')
            self.assertTrue(svg.headers['content-disposition'].startswith('attachment;'))
