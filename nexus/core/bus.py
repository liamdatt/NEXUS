from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any


Subscriber = Callable[[dict[str, Any]], Awaitable[None]]


class MessageBus:
    """Simple async pub/sub bus used for internal events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)

    def subscribe(self, event: str, callback: Subscriber) -> None:
        self._subscribers[event].append(callback)

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        if event not in self._subscribers:
            return
        await asyncio.gather(*(cb(payload) for cb in self._subscribers[event]))
