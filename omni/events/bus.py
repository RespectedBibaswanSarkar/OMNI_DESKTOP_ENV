from __future__ import annotations

import asyncio
import inspect
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Handler = Callable[["Event"], Any | Awaitable[Any]]


@dataclass(frozen=True)
class Event:
    topic: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """In-process asyncio pub/sub bus. Handlers run in subscription order."""

    TOPICS = frozenset({
        "context.update",
        "intent.decision",
        "modules.plan",
        "sched.load",
        "task.reward",
    })

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    async def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        if topic not in self.TOPICS:
            raise ValueError(f"Unknown OMNI event topic: {topic}")
        with self._lock:
            self._handlers[topic].append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(topic, [])
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def publish(self, topic: str, payload: dict[str, Any]) -> Event:
        if topic not in self.TOPICS:
            raise ValueError(f"Unknown OMNI event topic: {topic}")
        event = Event(topic=topic, payload=dict(payload))
        with self._lock:
            handlers = tuple(self._handlers.get(topic, ()))
        for handler in handlers:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        return event

    async def subscriber_count(self, topic: str) -> int:
        with self._lock:
            return len(self._handlers.get(topic, ()))
