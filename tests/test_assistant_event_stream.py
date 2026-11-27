"""订阅中的认证失效、归档、断连与不可信游标合同。"""
from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4

from assistant_run_support import RunFixture
from angemedia_gateway.repositories.admin_auth import delete_admin_session
from angemedia_gateway.repositories.assistant_conversations import AssistantConversationRepository
from angemedia_gateway.services.assistant_event_stream import event_cursor, run_event_stream
from angemedia_gateway.services.assistant_run_service import AssistantRunService


class EventStreamTest(RunFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = AssistantRunService(self.repo)
        await self.service.start()
        self.run_id = self.repo.admit(self.session, uuid4().hex, {}).run["id"]

    async def asyncTearDown(self):
        await self.service.aclose()

    async def stream(self):
        initial = await self.service.inspect(self.run_id, access=self.access["alice"])
        return run_event_stream(self.service, self.run_id, self.access["alice"], initial, poll_seconds=0.01)

    async def test_logout_ends_open_subscription_with_no_further_events(self):
        stream = await self.stream()
        self.assertIn("event: admitted", await anext(stream))
        delete_admin_session(self.cookies["alice"])
        self.assertIn('"code":"authentication_expired"', await anext(stream))
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)
        self.assertEqual(self.repo.get(self.run_id)["status"], "queued")

    async def test_archive_ends_subscription_without_reviving_run(self):
        stream = await self.stream()
        await anext(stream)
        AssistantConversationRepository().archive(self.session, username="alice")
        self.assertIn('"code":"run_not_found"', await anext(stream))
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    async def test_closing_subscription_does_not_cancel_the_run(self):
        stream = await self.stream()
        await anext(stream)
        await stream.aclose()
        self.assertEqual(self.repo.get(self.run_id)["status"], "queued")

    async def test_service_shutdown_closes_idle_subscription(self):
        stream = await self.stream()
        await anext(stream)
        await self.service.aclose()
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    def test_cursors_are_bounded_bound_to_run_and_unambiguous(self):
        for value in ("abc", f"{uuid4().hex}:1", f"{self.run_id}:-1", f"{self.run_id}:9223372036854775808",
                      f"{self.run_id}:1\nevent:fake", f"{self.run_id}:1:2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                event_cursor(self.run_id, None, value)
        self.assertEqual(event_cursor(self.run_id, 2, f"{self.run_id}:2"), 2)
        self.assertEqual(event_cursor(self.run_id, None, None), 0)


if __name__ == "__main__":
    unittest.main()
