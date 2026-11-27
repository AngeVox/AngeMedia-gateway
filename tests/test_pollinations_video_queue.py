"""用真实阶段、入队数据库和不联网的 HTTP 夹具验证视频交付。"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from assistant_action_worker_support import ActionWorkerFixture
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect, db_transaction
from angemedia_gateway.pollinations_video import POLLINATIONS_VIDEO_MODEL, PollinationsVideoRequest
from angemedia_gateway.queue.messages import JobStageMessage
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.pollinations_video_receipts import video_receipt
from angemedia_gateway.repositories.settings import set_builtin_provider_enabled
from angemedia_gateway.schemas import VideoRequest
from angemedia_gateway.services.job_stage_registry import default_job_stage_registry
from angemedia_gateway.services.local_worker_failure_recovery import LocalWorkerFailureRecovery
from angemedia_gateway.services.pollinations_video_files import pollinations_video_file, pollinations_video_visible
from angemedia_gateway.services.video_job_admission import VideoJobAdmissionService
from angemedia_gateway.services.worker_runtime import WorkerRuntime

TASK = "4f" * 32
ROOT = "https://gen.pollinations.ai"
STATUS = "/v1/jobs/" + TASK
KEY = "fixture-pollinations-credential-123456789"
MODULE = "angemedia_gateway.services.pollinations_video_worker"


def _box(kind, data):
    return (8 + len(data)).to_bytes(4, "big") + kind + data


MP4 = _box(b"ftyp", b"isom" + b"\0" * 4) + _box(b"moov", b"metadata") + _box(b"mdat", b"payload")


class PollinationsVideoQueueTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        set_builtin_provider_enabled("pollinations", True)
        auth = patch.object(C, "POLLINATIONS_API_KEY", KEY)
        auth.start()
        self.addCleanup(auth.stop)
        self.clock = datetime.now(timezone.utc)
        date = patch(f"{MODULE}.datetime", wraps=datetime)
        fake_date = date.start()
        fake_date.now.side_effect = lambda *_: self.clock
        self.addCleanup(date.stop)
        self.runtime = WorkerRuntime(registry=default_job_stage_registry(), runtime_refresher=lambda: None)

    def upstream(self, *responses):
        sequence = iter(responses)

        def answer(request):
            self.calls.append(request)
            self.assertEqual(request.headers["authorization"], f"Bearer {KEY}")
            self.assertEqual(request.url.host, "gen.pollinations.ai")
            item = next(sequence)
            if isinstance(item, BaseException):
                raise item
            return item

        mock = patch("angemedia_gateway.providers.pollinations_video.provider_client",
                     side_effect=lambda **_: httpx.AsyncClient(transport=httpx.MockTransport(answer)))
        mock.start()
        self.addCleanup(mock.stop)

    def submit(self, **fields):
        return VideoJobAdmissionService().submit(PollinationsVideoRequest(**self.request(**fields))).job

    def request(self, **fields):
        return {"provider": "pollinations", "model": POLLINATIONS_VIDEO_MODEL,
                "prompt": "猫和蓝色杯子 / A cat", **fields}

    def accepted(self, *, link=ROOT + STATUS):
        return httpx.Response(202, headers={"Location": link, "Retry-After": "7"},
                              json={"id": TASK, "status": "pending", "status_url": link})

    def video(self, content=MP4):
        return httpx.Response(200, headers={"Content-Type": "video/mp4"}, content=content)

    def stage(self, job, stage, attempt):
        with closing(db_connect()) as conn:
            for row in conn.execute("SELECT * FROM job_dispatches WHERE job_id=? ORDER BY rowid DESC", (job["id"],)):
                payload = json.loads(row["payload_json"])
                if (payload["stage"], payload["attempt"]) == (stage, attempt):
                    return JobStageMessage(job_id=job["id"], stage=stage, attempt=attempt,
                                           dispatch_id=row["id"], trace_id=row["id"]).to_dict()
        self.fail(f"No durable dispatch for {stage} attempt {attempt}")

    def handle(self, job, stage, attempt, *, advance=0):
        self.clock += timedelta(seconds=advance)
        return self.runtime.handle(self.stage(job, stage, attempt))["status"]

    def test_async_acceptance_pending_download_and_asset_are_durable(self):
        self.upstream(self.accepted(), httpx.Response(200, json={"id": TASK, "status": "pending"}),
                      httpx.Response(200, json={"id": TASK, "status": "succeeded", "result_url": STATUS + "/result"}),
                      self.video())
        job = self.submit(first_frame="https://example.org/first.png", last_frame="https://example.org/last.png")
        initial = self.stage(job, "video_submit", 1)
        self.assertEqual(self.runtime.handle(initial)["status"], "scheduled")
        self.assertEqual(self.runtime.handle(initial)["status"], "duplicate")
        recovery = LocalWorkerFailureRecovery()
        self.assertEqual(recovery.handle_duplicate(JobStageMessage(**initial)), "stale")
        self.assertEqual(get_job(job["id"])["external_task_id"], TASK)
        self.assertEqual(self.handle(job, "video_poll", 2), "not_due")
        self.assertEqual(self.handle(job, "video_poll", 2, advance=7), "scheduled")
        self.assertEqual(self.handle(job, "video_poll", 3, advance=10), "scheduled")
        self.assertEqual(self.handle(job, "asset_import", 4), "succeeded")
        call = self.calls[0]
        self.assertEqual(call.headers["prefer"], "respond-async")
        self.assertEqual(dict(call.url.params), {"model": POLLINATIONS_VIDEO_MODEL, "duration": "5",
                         "resolution": "480p", "image": "https://example.org/first.png|https://example.org/last.png"})
        self.assertIn(b"%2F", call.url.raw_path.split(b"?")[0])
        self.assertEqual([item.url.path for item in self.calls[1:]], [STATUS, STATUS, STATUS + "/result"])
        self.assertEqual(self.handle(job, "asset_import", 4), "duplicate")
        assets = list_assets(job_id=job["id"])
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["provider"], "pollinations")
        self.assertTrue(pollinations_video_visible(C.OUTPUT_DIR / assets[0]["relative_path"]))
        self.assertEqual((C.OUTPUT_DIR / assets[0]["relative_path"]).read_bytes(), MP4)
        with closing(db_connect()) as conn:
            stored = video_receipt(conn, job["id"])
            self.assertEqual(stored["task_id"], TASK)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM video_tasks").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM generations WHERE job_id=? AND provider='pollinations'",
                                         (job["id"],)).fetchone()[0], 1)

    def test_assistant_cached_result_has_no_external_id_and_no_second_http_call(self):
        self.upstream(self.video())
        action = self.propose(kind="video", request=self.request())
        self.assertIn("may_incur_cost", action["review"]["required_acknowledgements"])
        self.assertEqual(action["review"]["routes"][0]["provider"], "pollinations")
        job = self.job(action)
        self.assertEqual(self.handle(job, "video_submit", 1), "scheduled")
        staged = pollinations_video_file(job["id"], staged=True)
        self.assertTrue(staged.exists())
        self.assertFalse(pollinations_video_visible(staged))
        self.assertEqual(self.handle(job, "asset_import", 2), "succeeded")
        final = get_job(job["id"])
        self.assertFalse(final["external_task_id"])
        self.assertNotIn("task_id", json.loads(final["output_json"]))
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(staged.exists())

    def test_watchdog_resolves_process_interruption_without_repeating_submit(self):
        self.upstream(SystemExit())
        job = self.submit()
        with self.assertRaises(SystemExit):
            self.handle(job, "video_submit", 1)
        self.assertEqual(self.handle(job, "video_submit", 1), "inflight")
        duplicate = self.stage(job, "video_submit", 1)
        self.assertEqual(LocalWorkerFailureRecovery().handle_duplicate(JobStageMessage(**duplicate)), "watchdog_pending")
        self.assertEqual(self.handle(job, "video_submit", 1, advance=1000), "failed")
        self.assertEqual(get_job(job["id"])["error_code"], "pollinations_video_submit_unknown")
        self.assertEqual(len(self.calls), 1)

    def test_saved_receipt_recovers_if_poll_dispatch_transaction_fails(self):
        self.upstream(self.accepted())
        job = self.submit()
        from angemedia_gateway.repositories.job_dispatches import create_job_dispatch
        def dispatch(**kwargs):
            if kwargs["payload"]["stage"] == "video_poll":
                raise sqlite3.OperationalError("fixture outbox busy")
            return create_job_dispatch(**kwargs)
        with patch(f"{MODULE}.create_job_dispatch", side_effect=dispatch):
            self.assertEqual(self.handle(job, "video_submit", 1), "recovery_pending")
        with closing(db_connect()) as conn:
            self.assertEqual(video_receipt(conn, job["id"])["task_id"], TASK)
        self.assertEqual(self.handle(job, "video_submit", 1), "scheduled")
        self.assertEqual(get_job(job["id"])["external_task_id"], TASK)
        self.assertEqual(len(self.calls), 1)

    def test_retry_only_reads_and_rejects_changed_connection_before_request(self):
        self.upstream(self.accepted(), httpx.Response(429, headers={'Retry-After': '20'}), httpx.Response(503))
        job = self.submit()
        self.assertEqual(self.handle(job, "video_submit", 1), "scheduled")
        self.assertEqual(self.handle(job, "video_poll", 2, advance=7), "scheduled")
        self.assertEqual(self.handle(job, "video_poll", 3, advance=10), "not_due")
        self.assertEqual(self.handle(job, "video_poll", 3, advance=10), "scheduled")
        with patch.object(C, "POLLINATIONS_API_KEY", "fixture-different-token"):
            self.assertEqual(self.handle(job, "video_poll", 4, advance=30), "failed")
        self.assertEqual(get_job(job["id"])["error_code"], "pollinations_video_connection_changed")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual([request.url.path for request in self.calls[1:]], [STATUS] * 2)

    def test_foreign_links_and_invalid_mp4_never_publish(self):
        self.upstream(self.accepted(link="https://other.example" + STATUS),
                      httpx.Response(302, headers={"Location": "https://other.example/prompt"}),
                      self.video(MP4[:-1]))
        for index in range(3):
            job = self.submit(prompt=f"invalid response {index}")
            self.assertEqual(self.handle(job, "video_submit", 1), "failed")
            self.assertEqual(get_job(job["id"])["error_code"], "pollinations_video_submit_unknown")
            self.assertEqual(list_assets(job_id=job["id"]), [])
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(list(C.OUTPUT_DIR.rglob("*.part")))

    def test_followup_rejects_substituted_id_and_does_not_read_upstream(self):
        self.upstream(self.accepted())
        job = self.submit()
        self.assertEqual(self.handle(job, "video_submit", 1), "scheduled")
        with db_transaction(immediate=True) as conn:
            conn.execute("UPDATE jobs SET external_task_id=? WHERE id=?", ("ab" * 32, job["id"]))
        self.assertEqual(self.handle(job, "video_poll", 2, advance=7), "failed")
        self.assertEqual(get_job(job["id"])["error_code"], "pollinations_video_receipt_invalid")
        self.assertEqual(len(self.calls), 1)

    def test_import_rollback_hides_file_until_the_same_attempt_commits(self):
        self.upstream(self.video())
        job = self.submit()
        self.assertEqual(self.handle(job, "video_submit", 1), "scheduled")
        with patch(f"{MODULE}.finalize_imported_video", side_effect=sqlite3.OperationalError("fixture busy")):
            self.assertEqual(self.handle(job, "asset_import", 2), "recovery_pending")
        public = pollinations_video_file(job["id"])
        self.assertTrue(public.exists())
        self.assertFalse(pollinations_video_visible(public))
        self.assertEqual(self.handle(job, "asset_import", 2, advance=1000), "succeeded")
        self.assertTrue(pollinations_video_visible(public))
        self.assertEqual(len(self.calls), 1)

    def test_documented_rejections_have_distinct_diagnostics(self):
        statuses = ((401, "auth"), (402, "balance"), (403, "permission"), (429, "rate_limit"))
        self.upstream(*(httpx.Response(status, content=b"private fixture body") for status, _ in statuses))
        for status, code in statuses:
            job = self.submit(prompt=f"provider rejection {status}")
            self.assertEqual(self.handle(job, "video_submit", 1), "failed")
            current = get_job(job["id"])
            self.assertEqual(current["error_code"], "pollinations_video_" + code)
            self.assertNotIn("private fixture body", repr(current))
        self.assertEqual(len(self.calls), 4)

    def test_legacy_entry_and_unsupported_controls_reject_before_work(self):
        for model in (POLLINATIONS_VIDEO_MODEL, "wan-fast"):
            with self.assertRaises(ValidationError):
                VideoRequest(prompt="fixture", model=model)
        for kwargs in ({"seconds": "6"}, {"size": "720p"}, {"audios": ["https://example.org/audio.mp3"]},
                       {"last_frame": "https://example.org/end.png"}, {"wait_for_completion": True}):
            with self.assertRaises(ValidationError):
                PollinationsVideoRequest(**self.request(**kwargs))
        with self.assertRaisesRegex(ValueError, "stored safely"):
            self.submit(first_frame="https://example.org/image.png?token=fixture")
        self.assertEqual(len(self.calls), 0)
