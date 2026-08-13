from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any


class TransientProgressRegistry:
    """Bounded in-memory request progress for SSE; never becomes conversation history."""

    def __init__(self, *, max_per_project: int = 64, terminal_ttl: float = 30.0) -> None:
        self._items: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=max_per_project)
        )
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._terminal_ttl = terminal_ttl

    async def publish(
        self, project_id: str, request_id: str, stage: str, *, terminal: bool = False,
        error: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._sequence += 1
            item = {
                "sequence": self._sequence,
                "project_id": project_id,
                "request_id": request_id,
                "correlation_id": request_id,
                "stage": stage,
                "terminal": terminal,
                "error": error,
                "created_at": datetime.now(UTC).isoformat(),
                "_expires": time.monotonic() + self._terminal_ttl if terminal else None,
            }
            self._items[project_id].append(item)
            return {key: value for key, value in item.items() if not key.startswith("_")}

    async def since(self, project_id: str, sequence: int) -> list[dict[str, Any]]:
        async with self._lock:
            now = time.monotonic()
            queue = self._items.get(project_id, deque())
            retained = deque(
                (item for item in queue if item.get("_expires") is None or item["_expires"] > now),
                maxlen=queue.maxlen,
            )
            self._items[project_id] = retained
            return [
                {key: value for key, value in item.items() if not key.startswith("_")}
                for item in retained if int(item["sequence"]) > sequence
            ]
