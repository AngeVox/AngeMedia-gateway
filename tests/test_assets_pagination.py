"""作品分页的完整数据、组合条件、快照一致性与批量安全摘要回归。"""
from contextlib import contextmanager
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_assets_api as support
from angemedia_gateway.db.connection import db_transaction
from angemedia_gateway.repositories.assets import list_assets, count_assets, save_asset
from angemedia_gateway.state import create_job, record_generation
from angemedia_gateway.routes import storage


class AssetsPaginationTest(unittest.TestCase):
    setUp = support.AssetsApiTest.setUp
    tearDown = support.AssetsApiTest.tearDown
    login_admin = support.AssetsApiTest.login_admin
    _insert_test_asset = support.AssetsApiTest._insert_test_asset

    def seed(self, count=130, unique_jobs=False):
        rows = []
        with db_transaction() as conn:
            for i in range(count):
                row = {'id': f'asset-{i:03d}', 'media_type': 'video' if i < 5 or 120 <= i < 125 else 'image',
                       'source': 'upload' if i < 5 or i in (20, 40, 60, 80, 100) else 'generated',
                       'job_id': f'job-{i}' if unique_jobs else ('job-a' if i % 3 == 0 else 'job-b'), 'size': i + 1}
                save_asset(**row, filename=row['id']+'.png', storage_area='output', relative_path=row['id'],
                           url_path='/generated/'+row['id']+'.png', conn=conn)
                # 同时间戳仍须按编号稳定翻页，不依赖插入顺序。
                conn.execute('UPDATE assets SET created_at=? WHERE id=?', ('2026-01-01T00:00:00Z', row['id']))
                rows.append(row)
        return rows

    def test_legacy_default_data_and_full_summary(self):
        self.login_admin(); self.seed()
        body = self.client.get('/v1/assets').json()
        self.assertEqual(len(body['data']), 100)
        self.assertEqual(body['total'], 130)
        self.assertEqual(body['summary'], {'total': 130, 'image': 120, 'video': 10, 'size_bytes': 8515})
        self.assertEqual((body['limit'], body['offset']), (100, 0))

    def test_all_records_can_be_traversed_without_duplicates(self):
        self.login_admin(); rows = self.seed()
        ids = []
        for offset in range(0, 130, 31):
            body = self.client.get('/v1/assets', params={'limit': 31, 'offset': offset}).json()
            self.assertEqual(body['total'], 130)
            ids += [row['id'] for row in body['data']]
        self.assertEqual(ids, sorted((row['id'] for row in rows), reverse=True))

    def test_type_source_and_job_filters_share_count_and_aggregate_scope(self):
        self.login_admin(); rows = self.seed()
        for media in (None, 'image', 'video'):
            for source in (None, 'generated', 'upload'):
                for job in (None, 'job-a', 'missing', ''):
                    filters = {key: value for key, value in {'media_type': media, 'source': source, 'job_id': job}.items() if value is not None}
                    expected = [row for row in rows if all(row[key] == value for key, value in filters.items())]
                    with self.subTest(filters=filters):
                        body = self.client.get('/v1/assets', params={**filters, 'limit': 2}).json()
                        self.assertEqual(body['total'], len(expected))
                        self.assertEqual(body['summary']['image'], sum(row['media_type'] == 'image' for row in expected))
                        self.assertEqual(body['summary']['video'], sum(row['media_type'] == 'video' for row in expected))
                        self.assertEqual(body['summary']['size_bytes'], sum(row['size'] for row in expected))
                        self.assertTrue(all(all(row[key] == value for key, value in filters.items()) for row in body['data']))
                        self.assertEqual(count_assets(**filters), len(expected))
                        self.assertEqual(len(list_assets(limit=500, **filters)), len(expected))

    def test_out_of_range_page_retains_matching_total(self):
        self.login_admin(); self.seed()
        body = self.client.get('/v1/assets?limit=2&offset=999&media_type=video&source=upload').json()
        self.assertEqual(body['data'], [])
        self.assertEqual(body['total'], 5)
        self.assertEqual(body['summary']['size_bytes'], 15)
        injected = self.client.get('/v1/assets', params={'job_id': "' OR 1=1 --"}).json()
        self.assertEqual(injected['total'], 0)

    def test_invalid_query_values_are_rejected(self):
        self.login_admin()
        for params in ({'limit': 0}, {'limit': 501}, {'offset': -1}, {'media_type': 'all'},
                       {'media_type': ''}, {'source': 'remote'}, {'source': ''}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get('/v1/assets', params=params).status_code, 422)

    def test_list_still_requires_admin(self):
        self.assertEqual(self.client.get('/v1/assets?media_type=video&source=upload').status_code, 401)

    def test_page_uses_fixed_number_of_queries_and_one_transaction(self):
        self.login_admin(); self.seed(510, unique_jobs=True)
        queries = []
        real_transaction = db_transaction

        @contextmanager
        def traced():
            with real_transaction() as conn:
                conn.set_trace_callback(queries.append)
                yield conn

        with patch.object(storage, 'db_transaction', traced):
            body = self.client.get('/v1/assets?limit=500').json()
        self.assertEqual(len(body['data']), 500)
        self.assertEqual(body['total'], 510)
        selects = [sql for sql in queries if sql.lstrip().upper().startswith('SELECT')]
        self.assertEqual(len(selects), 4)
        self.assertIn('COMMIT', queries)

    def test_rows_statistics_and_links_share_snapshot_during_real_writer_commit(self):
        self.login_admin()
        job = create_job(kind='image', status='running', stage='image_poll', prompt='private fixture',
                         input_json='{"api_key":"sk-snapshot-secret-value"}')
        self._insert_test_asset('before', job_id=job['id'])
        record_generation(media_type='image', prompt='private fixture', enhanced_prompt=None, model='fixture',
                          status='running', result={}, job_id=job['id'])
        original = storage.summarize_assets

        def aggregate_then_commit(**kwargs):
            summary = original(**kwargs)
            writer = """
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as conn:
 conn.execute("INSERT INTO assets(id,filename,storage_area,relative_path,url_path,media_type,source,size,created_at) VALUES(?,?,?,?,?,?,?,?,?)",('after','after.png','output','after.png','/generated/after.png','image','generated',1,'2099-01-01'))
 conn.execute("UPDATE jobs SET status='failed' WHERE id=?",(sys.argv[2],))
 conn.execute("UPDATE generations SET status='failed' WHERE job_id=?",(sys.argv[2],))
"""
            subprocess.run([sys.executable, '-c', writer, str(self._db_path), job['id']], check=True, capture_output=True, timeout=20)
            return summary

        with patch.object(storage, 'summarize_assets', aggregate_then_commit):
            body = self.client.get('/v1/assets').json()
        self.assertEqual(body['total'], 1)
        self.assertEqual([row['id'] for row in body['data']], ['before'])
        self.assertEqual(body['data'][0]['job']['status'], 'running')
        self.assertEqual(body['data'][0]['generation']['status'], 'running')
        next_page = self.client.get('/v1/assets').json()
        self.assertEqual(next_page['total'], 2)
        linked = next(row for row in next_page['data'] if row['id'] == 'before')
        self.assertEqual(linked['job']['status'], 'failed')
        self.assertNotIn('sk-snapshot-secret-value', json.dumps(next_page))

    def test_bulk_summary_matches_existing_safe_projection(self):
        self.login_admin()
        job = create_job(kind='image', status='succeeded', prompt='safe fixture', output_json='{"local_path":"secret-path"}')
        self._insert_test_asset('safe', job_id=job['id'], url_path='https://example.invalid/p.png?token=secret-token')
        record_generation(media_type='image', prompt='safe fixture', enhanced_prompt=None, model='fixture', status='completed',
                          result={}, job_id=job['id'])
        bulk = self.client.get('/v1/assets').json()['data'][0]
        detail = self.client.get('/v1/assets/safe').json()['data']
        self.assertEqual(bulk, detail)
        text = json.dumps(bulk)
        for forbidden in ('storage_area', 'relative_path', 'input_json', 'output_json', 'secret-path', 'secret-token'):
            self.assertNotIn(forbidden, text)
