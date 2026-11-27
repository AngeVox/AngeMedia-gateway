"""Celery implementation of the broker-neutral queue publisher."""
from __future__ import annotations

from typing import Any

from .messages import JobStageMessage
from .settings import QueueSettings, WORKER_TASK_NAME


class QueueUnavailable(RuntimeError):
    pass


class CeleryQueueBackend:
    def __init__(self, *, app: Any, settings: QueueSettings) -> None:
        if not settings.enabled or settings.backend != "celery":
            raise RuntimeError("Celery queue backend is not enabled")
        self.app = app
        self.settings = settings

    def publish(self, *, topic: str, message: JobStageMessage) -> str:
        if topic != WORKER_TASK_NAME:
            raise ValueError("unapproved Celery task topic")
        if not isinstance(message, JobStageMessage):
            raise TypeError("CeleryQueueBackend requires JobStageMessage")
        result = self.app.send_task(
            topic,
            args=[message.to_dict()],
            queue=self.settings.task_queue,
            serializer="json",
            ignore_result=True,
            task_id=message.dispatch_id,
            argsrepr="(<sanitized-job-stage-message>,)",
            kwargsrepr="{}",
        )
        return str(result.id)

    def revoke(self, broker_message_id: str) -> None:
        self.app.control.revoke(str(broker_message_id), terminate=False)

    def healthcheck(self) -> None:
        connection = None
        try:
            # 健康探测独立限制连接与Redis读写时限，不改变正常发布连接。
            connection = self.app.connection_for_write(connect_timeout=3,
                transport_options={"socket_connect_timeout": 3, "socket_timeout": 3})
            connection.ensure_connection(max_retries=0, timeout=3)
        except Exception:
            raise QueueUnavailable("queue broker unavailable") from None
        finally:
            if connection is not None:
                try:
                    connection.release()
                except Exception:
                    pass
