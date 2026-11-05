from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    """Simple fan-out bus for SSE live updates."""

    def __init__(self) -> None:
        self._subs: list[asyncio.Queue[dict[str, Any]]] = []

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        if q in self._subs:
            self._subs.remove(q)

    async def publish(self, event: dict[str, Any]) -> None:
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for q in list(self._subs):
            try:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(event)
            except Exception:  # noqa: BLE001
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)
