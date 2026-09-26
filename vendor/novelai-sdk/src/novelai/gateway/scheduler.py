"""Round-robin single-flight scheduler for multi-key routing."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

from novelai import AsyncNovelAI


@dataclass(slots=True)
class KeySlot:
    """A single upstream NovelAI key and its bound async client."""

    index: int
    api_key: str
    client: AsyncNovelAI
    busy: bool = False


class KeyLease:
    """Context-managed key lease."""

    def __init__(self, scheduler: KeyScheduler, slot: KeySlot):
        self._scheduler = scheduler
        self.slot = slot
        self._released = False

    @property
    def client(self) -> AsyncNovelAI:
        return self.slot.client

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._scheduler.release(self.slot)

    async def __aenter__(self) -> KeyLease:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.release()


class KeyScheduler:
    """FIFO queue + round-robin key selection with per-key single concurrency."""

    def __init__(self, api_keys: list[str], queue_timeout_seconds: float = 15.0):
        if not api_keys:
            raise ValueError("At least one API key is required")

        self.queue_timeout_seconds = queue_timeout_seconds
        self._slots = [
            KeySlot(index=i, api_key=api_key, client=AsyncNovelAI(api_key=api_key))
            for i, api_key in enumerate(api_keys)
        ]
        self._condition = asyncio.Condition()
        self._waiters: deque[object] = deque()
        self._next_index = 0

    @property
    def total_keys(self) -> int:
        return len(self._slots)

    @property
    def busy_keys(self) -> int:
        return sum(1 for slot in self._slots if slot.busy)

    @property
    def idle_keys(self) -> int:
        return self.total_keys - self.busy_keys

    @property
    def queued_requests(self) -> int:
        return len(self._waiters)

    async def close(self) -> None:
        for slot in self._slots:
            await slot.client.close()

    def _pick_next_idle_slot(self) -> KeySlot | None:
        total = len(self._slots)
        for offset in range(total):
            index = (self._next_index + offset) % total
            slot = self._slots[index]
            if not slot.busy:
                self._next_index = (index + 1) % total
                return slot
        return None

    async def acquire(self, timeout: float | None = None) -> KeyLease:
        """Acquire an idle key, waiting in FIFO order up to the timeout."""
        effective_timeout = timeout or self.queue_timeout_seconds
        waiter = object()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + effective_timeout

        async with self._condition:
            self._waiters.append(waiter)

            try:
                while True:
                    if self._waiters and self._waiters[0] is waiter:
                        slot = self._pick_next_idle_slot()
                        if slot is not None:
                            slot.busy = True
                            self._waiters.popleft()
                            return KeyLease(self, slot)

                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        self._remove_waiter(waiter)
                        self._condition.notify_all()
                        raise TimeoutError(
                            "Timed out waiting for an available upstream key"
                        )

                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                self._remove_waiter(waiter)
                self._condition.notify_all()
                raise TimeoutError(
                    "Timed out waiting for an available upstream key"
                ) from exc
            except Exception:
                self._remove_waiter(waiter)
                self._condition.notify_all()
                raise

    async def release(self, slot: KeySlot) -> None:
        async with self._condition:
            slot.busy = False
            self._condition.notify_all()

    def _remove_waiter(self, waiter: object) -> None:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            pass