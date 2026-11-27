"""任务展示翻译与升级缓存的运行时回归；浏览器交互另留真实证据。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / 'app/www/assets/studio'
sys.path.insert(0, str(ROOT / 'scripts'))

RUNNER = r"""
import fs from 'node:fs';
import vm from 'node:vm';
import { setLanguage } from './i18n.js';
import * as display from './lib/job-display.js';
import { formatDate, formatDuration } from './lib/format.js';
const [language, method, args] = process.argv.slice(2);
setLanguage(language);
let result;
if (method === 'keys') {
  const source = fs.readFileSync(new URL('./i18n.js', import.meta.url), 'utf8').replace(/export \{[^}]+\};/, '');
  const dictionaries = vm.runInNewContext(source + ';({zhCN,enUS})');
  result = Object.fromEntries(Object.entries(dictionaries).map(([lang, dict]) =>
    [lang, Object.keys(dict).filter(key => key.startsWith('jobDisplay.') || key.startsWith('jobs.technicalDetails')).sort()]));
} else result = ({formatDate, formatDuration}[method] || display[method])(...JSON.parse(args));
process.stdout.write(JSON.stringify(result));
"""


@unittest.skipUnless(shutil.which('node'), '需要 Node.js 执行实际展示模块')
class JobDisplayRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temp.name)
        (cls.work / 'lib').mkdir()
        for name in ('i18n.js', 'lib/job-display.js', 'lib/format.js'):
            shutil.copyfile(STUDIO / name, cls.work / name)
        (cls.work / 'package.json').write_text('{"type":"module"}')
        (cls.work / 'runner.mjs').write_text(RUNNER, encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def call(self, lang, method, *args):
        result = subprocess.run(['node', str(self.work / 'runner.mjs'), lang, method, json.dumps(args)],
                                capture_output=True, text=True, check=True, encoding='utf-8')
        return json.loads(result.stdout)

    def test_task_display_keys_are_complete_in_both_languages(self):
        keys = self.call('zh-CN', 'keys')
        self.assertEqual(keys['zhCN'], keys['enUS'])
        self.assertGreater(len(keys['zhCN']), 100)

    def test_settings_have_localized_names_and_values(self):
        for lang, name, auto, builtin in [('zh-CN', '图片尺寸', '自动选择', '内置渠道'),
                                          ('en-US', 'Image size', 'Automatic', 'Built-in channel')]:
            with self.subTest(lang=lang):
                self.assertEqual(self.call(lang, 'displayJobSummaryKey', 'size'), name)
                self.assertEqual(self.call(lang, 'displayJobSummaryValue', 'operation', 'auto'), auto)
                self.assertEqual(self.call(lang, 'displayJobSummaryValue', 'route_mode', 'builtin'), builtin)

    def test_known_stages_events_and_existing_special_cases_are_translated(self):
        for lang, stage, event, poll in [('zh-CN', '整理任务结果', '这一步已完成', '等待图片结果'),
                                        ('en-US', 'Finalizing result', 'Finished this step', 'Waiting for image result')]:
            with self.subTest(lang=lang):
                self.assertEqual(self.call(lang, 'displayJobStage', 'FINALIZE'), stage)
                self.assertEqual(self.call(lang, 'displayJobEventType', 'worker_attempt_succeeded'), event)
                self.assertEqual(self.call(lang, 'displayJobStage', 'IMAGE_POLL'), poll)

    def test_user_content_model_and_unknown_identifiers_remain_recognizable(self):
        for lang in ('zh-CN', 'en-US'):
            self.assertEqual(self.call(lang, 'displayJobSummaryValue', 'model', 'my-Model_v2'), 'my-Model_v2')
            self.assertEqual(self.call(lang, 'displayJobProvider', 'Custom_Channel'), 'Custom_Channel')
            self.assertEqual(self.call(lang, 'displayJobSummaryValue', 'prompt', 'a cat on a table'), 'a cat on a table')
            self.assertEqual(self.call(lang, 'displayJobStage', 'future_stage'), 'future stage')

    def test_dates_follow_selected_language_and_durations_share_formatter(self):
        date = '2026-11-01T12:00:00Z'
        for lang, expected in [('zh-CN', '14.2秒'), ('en-US', '14.2 sec')]:
            duration = self.call(lang, 'formatDuration', 14210)
            self.assertEqual(duration, expected)
            self.assertEqual(self.call(lang, 'displayJobSummaryValue', 'duration_ms', 14210), duration)
            self.assertEqual(self.call(lang, 'displayJobSummaryValue', 'created_at', 'invalid-date'), '-')
            self.assertEqual(self.call(lang, 'displayJobSummaryValue', 'created_at', date),
                             self.call(lang, 'formatDate', date))
        self.assertNotEqual(self.call('zh-CN', 'formatDate', date), self.call('en-US', 'formatDate', date))


class FrontendCacheTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from studio_test_client import TestClient
        from angemedia_gateway.server import RevalidatingStaticFiles
        self.temp = tempfile.TemporaryDirectory()
        self.file = Path(self.temp.name) / 'app.js'
        self.file.write_text('old script')
        app = FastAPI()
        app.mount('/assets', RevalidatingStaticFiles(directory=self.temp.name))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_static_body_and_conditional_response_require_revalidation(self):
        first = self.client.get('/assets/app.js')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers['cache-control'], 'no-cache')
        unchanged = self.client.get('/assets/app.js', headers={'If-None-Match': first.headers['etag']})
        self.assertEqual(unchanged.status_code, 304)
        self.assertEqual(unchanged.headers['cache-control'], 'no-cache')

    def test_existing_snapshot_stays_consistent_and_new_snapshot_has_changed_bytes(self):
        first = self.client.get('/assets/app.js')
        self.file.write_text('new upgraded script')
        unchanged = self.client.get('/assets/app.js', headers={'If-None-Match': first.headers['etag']})
        self.assertEqual(unchanged.status_code, 304)
        from fastapi import FastAPI
        from studio_test_client import TestClient
        from angemedia_gateway.server import RevalidatingStaticFiles
        app = FastAPI()
        app.mount('/assets', RevalidatingStaticFiles(directory=self.temp.name))
        with TestClient(app) as current:
            changed = current.get('/assets/app.js', headers={'If-None-Match': first.headers['etag']})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.text, 'new upgraded script')
        self.assertNotEqual(changed.headers['etag'], first.headers['etag'])
