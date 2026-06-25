from __future__ import annotations

import asyncio


class JobProgressBus:
    """In-memory pub/sub bus for job progress events (one asyncio.Queue per subscriber)."""

    def __init__(self):
        self._subs: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(job_id, []).append(q)
        return q

    async def publish(self, job_id: str, event: dict) -> None:
        for q in self._subs.get(job_id, []):
            await q.put(event)

    def remove(self, job_id: str) -> None:
        self._subs.pop(job_id, None)


# 进程级单例（模块全局）
progress_bus = JobProgressBus()
