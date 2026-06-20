"""Broker-neutral queue contracts; concrete transports live outside domain services."""

from .contracts import QueueBackend, QueueDispatchEnvelope

__all__ = ["QueueBackend", "QueueDispatchEnvelope"]
