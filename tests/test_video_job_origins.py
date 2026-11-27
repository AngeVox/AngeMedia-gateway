"""来源收据约束真实普通队列和助手队列，不将当前账户追认为历史来源。"""
import base64
from contextlib import closing, contextmanager
import json
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from video_job_origin_support import VideoOriginFixture
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect, db_transaction
from angemedia_gateway.media_execution_scope import current_execution_scope
from angemedia_gateway.providers.runtime_config import resolve_provider_runtime_config
from angemedia_gateway.providers.transport_config import resolve_saved_provider_transport
from angemedia_gateway.reference_images import materialize_gateway_image_reference
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.provider_transport_config import update_provider_transport_config
from angemedia_gateway.repositories.video_job_origins import (VideoOriginRejected, bind_video_job_origin,
    external_task_digest, get_video_job_origin, record_video_job_origin)
from angemedia_gateway.services.media_connection_binding import connection_binding
from angemedia_gateway.adapters.agnes_video import AgnesVideoProvider
from angemedia_gateway.services.video_execution import VideoExecutionService


class VideoJobOriginsTest(VideoOriginFixture, unittest.TestCase):
    def test_ordinary_queue_retains_one_source_through_submit_poll_import_and_replay(self):
        job, runtime = self.submitted_origin()
        origin = get_video_job_origin(job['id'])
        self.assertEqual(origin['connection_digest'], connection_binding('agnes_video'))
        self.assertEqual(origin['external_task_digest'], external_task_digest('action-video-task'))
        for expected in ('scheduled', 'succeeded'):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], expected)
        self.assertEqual(get_video_job_origin(job['id']), origin)
        self.assertEqual([row[0] for row in self.calls], ['submit', 'poll', 'poll', 'import'])
        self.assertIn(runtime.handle(self.message(job['id']))['status'], {'terminal', 'duplicate'})
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM assistant_action_execution_grants').fetchone()[0], 0)

    def test_connection_changes_wait_without_io_and_restore_resumes_original_task(self):
        job, runtime = self.submitted_origin()
        before = get_video_job_origin(job['id'])
        for changes in ({'api_key': 'other-account-fixture'}, {'base_url_override': 'https://other.invalid/v1'}):
            with self.subTest(changes=tuple(changes)):
                update_provider_runtime_config('agnes_video', **changes)
                self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
                current = get_job(job['id'])
                self.assertEqual(current['error_code'], 'video_origin_connection_changed')
                self.assertEqual(current['error_category'], 'provider_identity')
                self.assertEqual(current['provider_status'], 'queued')
                self.assertEqual(current['external_task_id'], 'action-video-task')
                self.assertEqual(len(self.calls), 1)
                update_provider_runtime_config('agnes_video', api_key=None, base_url_override=None)
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'succeeded')
        self.assertEqual(get_video_job_origin(job['id']), before)
        self.assertEqual([row[0] for row in self.calls].count('submit'), 1)

    def test_shared_credential_and_transport_change_are_also_connection_changes(self):
        job, runtime = self.submitted_origin()
        with patch.object(C, 'AGNES_API_KEY', 'changed-shared-credential'):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        update_provider_transport_config('agnes_video', transport_mode='explicit_proxy', proxy_url='http://127.0.0.1:12345')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(len(self.calls), 1)
        update_provider_transport_config('agnes_video', transport_mode=None, proxy_url=None)
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')

    def test_same_external_id_from_different_accounts_has_separate_job_origins(self):
        first, _ = self.submitted_origin(prompt='first-account-intent')
        update_provider_runtime_config('agnes_video', api_key='second-account-fixture')
        second, runtime = self.submitted_origin(prompt='second-account-intent')
        self.assertEqual(first['external_task_id'], second['external_task_id'])
        self.assertNotEqual(get_video_job_origin(first['id'])['connection_digest'],
                            get_video_job_origin(second['id'])['connection_digest'])
        self.assertEqual(runtime.handle(self.message(first['id']))['status'], 'scheduled')
        self.assertEqual([row[0] for row in self.calls], ['submit', 'submit'])
        self.assertEqual(runtime.handle(self.message(second['id']))['status'], 'scheduled')
        self.assertEqual(self.calls[-1], ('poll', 'action-video-task'))

    def test_import_connection_change_waits_without_poll_or_download(self):
        job, runtime = self.submitted_origin()
        runtime.handle(self.message(job['id']))
        update_provider_runtime_config('agnes_video', api_key='changed-import-account')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual([row[0] for row in self.calls], ['submit', 'poll'])
        self.assertEqual(get_job(job['id'])['provider_status'], 'completed')
        update_provider_runtime_config('agnes_video', api_key=None)
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'succeeded')

    def test_connection_wait_has_bounded_budget_and_terminal_job_is_not_revived(self):
        job, runtime = self.submitted_origin()
        worker = runtime.registry.get('asset_import').__self__
        runtime.handle(self.message(job['id']))
        update_provider_runtime_config('agnes_video', api_key='changed-final-account')
        with patch.object(type(worker.policy), 'exhausted', return_value=True):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(get_job(job['id'])['provider_status'], 'completed')
        update_provider_runtime_config('agnes_video', api_key=None)
        self.assertIn(runtime.handle(self.message(job['id']))['status'], {'terminal', 'duplicate'})
        self.assertEqual([row[0] for row in self.calls], ['submit', 'poll'])

    def test_runtime_transport_and_local_reference_are_frozen_for_actual_call(self):
        image = self.uploads / 'origin.png'
        content = b'\x89PNG\r\n\x1a\noriginal-frame'
        image.write_bytes(content)
        job = self.ordinary_job(image='/uploads/origin.png')
        before = resolve_provider_runtime_config('agnes_video')
        transport = resolve_saved_provider_transport('agnes_video')
        seen = []
        def change(request):
            update_provider_runtime_config('agnes_video', api_key='changed-during-request')
            update_provider_transport_config('agnes_video', transport_mode='explicit_proxy', proxy_url='http://127.0.0.1:12345')
            image.write_bytes(b'\x89PNG\r\n\x1a\nchanged-frame')
            seen.append((resolve_provider_runtime_config('agnes_video'), resolve_saved_provider_transport('agnes_video'),
                         base64.b64decode(materialize_gateway_image_reference(request.image).partition(',')[2])))
        self.assertEqual(self.video_runtime(on_submit=change).handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(seen, [(before, transport, content)])
        self.assertIsNone(current_execution_scope())
        self.assertNotEqual(get_video_job_origin(job['id'])['connection_digest'], connection_binding('agnes_video'))

    def test_task_id_tampering_blocks_query(self):
        job, runtime = self.submitted_origin()
        with closing(db_connect()) as conn:
            conn.execute('UPDATE jobs SET external_task_id=? WHERE id=?', ('other-task', job['id']))
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(get_job(job['id'])['error_code'], 'video_origin_task_changed')
        self.assertEqual(len(self.calls), 1)

    def test_connection_change_after_scope_creation_is_caught_at_final_io_gate(self):
        from angemedia_gateway.services import video_job_origin
        job, runtime = self.submitted_origin()
        original = video_job_origin.media_execution_scope
        @contextmanager
        def change(snapshot):
            with original(snapshot):
                update_provider_runtime_config('agnes_video', api_key='change-before-final-gate')
                yield
        with patch.object(video_job_origin, 'media_execution_scope', side_effect=change):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(get_job(job['id'])['error_code'], 'video_origin_connection_changed')
        self.assertEqual(len(self.calls), 1)

    def test_real_adapter_uses_recorded_headers_url_and_image_after_late_changes(self):
        image = self.uploads / 'adapter.png'; content = b'\x89PNG\r\n\x1a\nadapter-frame'; image.write_bytes(content)
        job = self.ordinary_job(image='/uploads/adapter.png'); runtime = self.video_runtime()
        provider = AgnesVideoProvider('unused', 'https://unused.invalid/v1')
        before = resolve_provider_runtime_config('agnes_video'); prepare = provider._prepare_runtime_request
        async def change(request):
            update_provider_runtime_config('agnes_video', api_key='late-adapter-account', base_url_override='https://late.invalid/v1')
            image.write_bytes(b'\x89PNG\r\n\x1a\nchanged-frame')
            return await prepare(request)
        worker = runtime.registry.get('video_submit').__self__; worker.executor = VideoExecutionService(provider=provider)
        with patch.object(provider, '_prepare_runtime_request', side_effect=change), \
                patch.object(provider, '_request_json', new=AsyncMock(return_value={'task_id': 'adapter-task', 'status': 'queued'})) as request:
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(request.call_args.args[1], before.base_url + '/videos')
        self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer ' + before.api_key)
        self.assertEqual(base64.b64decode(request.call_args.kwargs['json']['image']), content)
        self.assertEqual(get_video_job_origin(job['id'])['external_task_digest'], external_task_digest('adapter-task'))

    def test_intent_tampering_blocks_query(self):
        job, runtime = self.submitted_origin()
        payload = json.loads(job['input_json']); payload['request']['prompt'] = '换过的意图'
        with closing(db_connect()) as conn:
            conn.execute('UPDATE jobs SET input_json=? WHERE id=?', (json.dumps(payload), job['id']))
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(get_job(job['id'])['error_code'], 'video_origin_job_changed')
        self.assertEqual(len(self.calls), 1)

    def test_failure_to_record_source_prevents_submit(self):
        job = self.ordinary_job()
        with patch('angemedia_gateway.services.video_job_origin.record_video_job_origin', side_effect=sqlite3.OperationalError('fixture disk full')):
            self.assertEqual(self.video_runtime().handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(self.calls, [])
        self.assertIsNone(get_video_job_origin(job['id']))

    def test_no_result_receipt_cannot_be_reused_for_another_submission(self):
        job = self.ordinary_job()
        with db_transaction(immediate=True) as conn:
            record_video_job_origin(conn, job, connection_binding('agnes_video'))
        self.assertEqual(self.video_runtime().handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(get_job(job['id'])['error_code'], 'video_origin_submit_already_attempted')
        self.assertEqual(self.calls, [])

    def test_submit_dispatch_failure_binds_receipt_with_existing_recovery_transaction(self):
        job = self.ordinary_job(); runtime = self.video_runtime()
        worker = runtime.registry.get('video_submit').__self__
        with patch.object(worker, '_dispatch_stage', side_effect=RuntimeError('fixture outbox failure')):
            with self.assertRaises(RuntimeError): runtime.handle(self.message(job['id']))
        current = get_job(job['id'])
        self.assertEqual(current['external_task_id'], 'action-video-task')
        self.assertEqual(get_video_job_origin(job['id'])['external_task_digest'], external_task_digest('action-video-task'))
        for expected in ('scheduled', 'scheduled', 'succeeded'):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], expected)
        self.assertEqual([row[0] for row in self.calls].count('submit'), 1)

    def test_source_and_dispatch_rollback_together_before_recovery(self):
        job = self.ordinary_job(); runtime = self.video_runtime()
        worker = runtime.registry.get('video_submit').__self__
        with patch.object(worker, '_dispatch_stage', side_effect=RuntimeError('fixture outbox failure')), \
                patch.object(worker, '_record_submitted_recovery', side_effect=RuntimeError('fixture recovery unavailable')):
            with self.assertRaises(RuntimeError): runtime.handle(self.message(job['id']))
        self.assertIsNone(get_job(job['id'])['external_task_id'])
        self.assertIsNone(get_video_job_origin(job['id'])['external_task_digest'])
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'duplicate')
        self.assertEqual(len(self.calls), 1)

    def test_receipt_is_immutable_single_bind_and_survives_job_cleanup_without_secrets(self):
        job, runtime = self.submitted_origin()
        origin = get_video_job_origin(job['id'])
        with db_transaction(immediate=True) as conn:
            bind_video_job_origin(conn, job['id'], 'action-video-task')
            with self.assertRaises(VideoOriginRejected): bind_video_job_origin(conn, job['id'], 'different-task')
        with closing(db_connect()) as conn:
            for sql in ('DELETE FROM video_job_origins', "UPDATE video_job_origins SET model='changed'",
                        "UPDATE video_job_origins SET external_task_digest='" + 'a' * 64 + "'",
                        'INSERT OR REPLACE INTO video_job_origins SELECT * FROM video_job_origins'):
                with self.subTest(sql=sql[:40]), self.assertRaises(sqlite3.IntegrityError): conn.execute(sql)
            conn.execute('DELETE FROM jobs WHERE id=?', (job['id'],))
        self.assertEqual(get_video_job_origin(job['id']), origin)
        rendered = json.dumps(origin)
        for secret in (job['prompt'], C.AGNES_API_KEY, 'https://', 'action-video-task'):
            self.assertNotIn(secret, rendered)

    def test_old_generic_followup_cannot_guess_current_connection(self):
        job, runtime = self.submitted_origin()
        with closing(db_connect()) as conn:
            conn.execute('DROP TRIGGER video_job_origin_retained')
            conn.execute('DELETE FROM video_job_origins')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(get_job(job['id'])['error_code'], 'video_origin_unverified')
        self.assertEqual(len(self.calls), 1)

    def test_old_assistant_followup_uses_original_grant_without_backfilling_source(self):
        job = self.job(self.video_action()); runtime = self.video_runtime()
        runtime.handle(self.message(job['id']))
        with closing(db_connect()) as conn:
            conn.execute('DROP TRIGGER video_job_origin_retained')
            conn.execute('DELETE FROM video_job_origins')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'succeeded')
        self.assertIsNone(get_video_job_origin(job['id']))

    def test_assistant_connection_rejection_remains_terminal_and_not_a_generic_wait(self):
        job = self.job(self.video_action()); runtime = self.video_runtime()
        runtime.handle(self.message(job['id']))
        update_provider_runtime_config('agnes_video', api_key='changed-assistant-account')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(get_job(job['id'])['error_code'], 'action_execution_connection_changed')
        self.assertEqual(len(self.calls), 1)
