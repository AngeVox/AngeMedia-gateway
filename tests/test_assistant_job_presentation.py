"""任务观察投影与真实阶段转换，避免自然文案以未经观察的状态误报完成。"""
from contextlib import closing
import json
import unittest

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.presenters.assistant_jobs import assistant_job_observation
from angemedia_gateway.repositories.assets import list_assets


class AssistantJobPresentationTest(ActionWorkerFixture, unittest.TestCase):
    def test_task_progress_follows_committed_video_worker_stages(self):
        action = self.propose(kind='video', request={'prompt':'自然光下的山谷', 'model':'agnes-video-v2.5', 'seconds':'6'})
        job_id = self.confirm(action)['action']['job']['id']; runtime = self.video_runtime()
        def observation(): return self.service.inspect(action['id'], access=self.alice)['job']
        first = observation(); self.assertEqual((first['status'],first['stage'],first['external_task_present']), ('queued','video_submit',False))
        for stage, status in (('video_poll','running'),('asset_import','running'),('finalize','succeeded')):
            runtime.handle(self.message(job_id)); current = observation()
            self.assertEqual((current['stage'],current['status'],current['external_task_present']), (stage,status,True))
            self.assertNotIn('external_task_id', current)
        self.assertEqual(len(list_assets(job_id=job_id)),1)

    def test_unknown_and_sensitive_codes_never_leave_the_observation(self):
        result = assistant_job_observation({'id':'a'*32, 'status':'failed', 'kind':'image', 'stage':'secret-stage',
            'error_code':'Authorization: Bearer fixture-secret', 'error_category':'private-category',
            'external_task_id':'external-secret', 'human_hint':'private hint', 'output_json':'private output'})
        text = json.dumps(result)
        for value in ('secret-stage','fixture-secret','private-category','external-secret','private hint','private output'):
            self.assertNotIn(value,text)
        self.assertEqual(result['error_code'],'unknown'); self.assertTrue(result['external_task_present'])

    def test_job_observation_is_only_returned_to_its_action_owner(self):
        action = self.propose(); self.confirm(action)
        self.assert_code('action_not_found',self.service.inspect,action['id'],access=self.bob)
        with closing(db_connect()) as conn: conn.execute('DELETE FROM jobs WHERE id=?',(self.service.inspect(action['id'],access=self.alice)['job']['id'],))
        self.assertEqual(self.service.inspect(action['id'],access=self.alice)['job']['status'],'record_unavailable')
