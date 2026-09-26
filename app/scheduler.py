"""FIFO key scheduler with one in-flight request per upstream key.

This is deliberately simple.  BestNAI's job is to hand out a free key, keep the
request on that key until it finishes, and put it back.  There is no request
classification, no tier-aware routing, and no automatic key quarantine — a key
participates in scheduling exactly when it is enabled.

Waiting is strict FIFO: a request may only take a key when it is at the head of
the queue, so an earlier request can never be overtaken.  When no key frees up
before the deadline, `acquire()` raises `TimeoutError`; the API layer turns that
into HTTP 503.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .client import NovelAIClient
from .key_policy import key_hash, mask_key


@dataclass
class KeySlot:
    """One upstream key and its runtime state."""

    api_key: str
    index: int
    client: NovelAIClient
    enabled: bool = True
    busy: bool = False
    note: str = ""
    key_id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.key_id:
            self.key_id = key_hash(self.api_key)

    def public(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "index": self.index,
            "masked_key": mask_key(self.api_key),
            "enabled": self.enabled,
            "busy": self.busy,
            "note": self.note,
        }


class KeyLease:
    """A single-shot lease over one key slot."""

    def __init__(self, scheduler: "KeyScheduler", slot: KeySlot) -> None:
        self.scheduler = scheduler
        self.slot = slot
        self.released = False

    @property
    def client(self) -> NovelAIClient:
        return self.slot.client

    @property
    def key_id(self) -> str:
        return self.slot.key_id

    async def release(self) -> None:
        if not self.released:
            self.released = True
            await self.scheduler.release(self.slot)

    async def __aenter__(self) -> "KeyLease":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.release()


class KeyScheduler:
    """Strict FIFO queue with one in-flight request per upstream key."""

    def __init__(
        self,
        api_keys: list[str] | None = None,
        *,
        timeout: float = 30.0,
        image_base: str | None = None,
        api_base: str | None = None,
        client_factory: Any = None,
    ) -> None:
        self.timeout = timeout
        self._image_base = image_base
        self._api_base = api_base
        self._client_factory = client_factory or self._default_client_factory
        self.slots: list[KeySlot] = []
        self._cond = asyncio.Condition()
        self._waiters: deque[object] = deque()
        self._retiring: list[KeySlot] = []
        self._next = 0
        if api_keys:
            self.slots = [
                KeySlot(key, index, self._client_factory(key))
                for index, key in enumerate(api_keys)
            ]

    def _default_client_factory(self, api_key: str) -> NovelAIClient:
        return NovelAIClient(
            api_key,
            **({"image_base": self._image_base} if self._image_base else {}),
            **({"api_base": self._api_base} if self._api_base else {}),
        )

    # ── Introspection ────────────────────────────────────────────────────────

    @property
    def total_keys(self) -> int:
        return len(self.slots)

    @property
    def busy_keys(self) -> int:
        return sum(slot.busy and slot.enabled for slot in self.slots) + sum(
            slot.busy for slot in self._retiring
        )

    @property
    def idle_keys(self) -> int:
        return sum((not slot.busy) and slot.enabled for slot in self.slots)

    @property
    def disabled_keys(self) -> int:
        return sum(not slot.enabled for slot in self.slots)

    @property
    def queued_requests(self) -> int:
        return len(self._waiters)

    def list_keys(self) -> list[dict[str, Any]]:
        return [slot.public() for slot in self.slots]

    def get_slot(self, key_id: str) -> KeySlot | None:
        return next((slot for slot in self.slots if slot.key_id == key_id), None)

    def get_key(self, key_id: str) -> dict[str, Any]:
        slot = self.get_slot(key_id)
        if slot is None:
            raise KeyError(f"Unknown key_id: {key_id}")
        return slot.public()

    def health(self) -> dict[str, Any]:
        return {
            "mode": "pooled-keys" if self.slots else "byok-only",
            "total_keys": self.total_keys,
            "busy_keys": self.busy_keys,
            "idle_keys": self.idle_keys,
            "disabled_keys": self.disabled_keys,
            "queued_requests": self.queued_requests,
        }

    # ── Pool reconciliation ──────────────────────────────────────────────────

    async def replace_keys(self, key_records: list[dict[str, Any]]) -> bool:
        """Reconcile the in-memory pool against the durable enabled key set.

        Incoming records are always enabled: a disabled key is simply absent from
        the input.  Keys that disappear while busy are retired once their
        in-flight request finishes.
        """
        normalized = [record for record in key_records if record.get("api_key")]
        close_now: list[KeySlot] = []
        async with self._cond:
            existing = {slot.api_key: slot for slot in [*self.slots, *self._retiring]}
            incoming = {record["api_key"] for record in normalized}
            new_slots: list[KeySlot] = []
            for index, record in enumerate(normalized):
                slot = existing.get(record["api_key"])
                if slot is None:
                    slot = KeySlot(
                        api_key=record["api_key"],
                        index=index,
                        client=self._client_factory(record["api_key"]),
                    )
                elif slot in self._retiring:
                    self._retiring.remove(slot)
                slot.index = index
                slot.enabled = True
                slot.note = str(record.get("note") or "")
                slot.key_id = str(record.get("key_id") or slot.key_id)
                new_slots.append(slot)

            for slot in self.slots:
                if slot.api_key in incoming:
                    continue
                slot.enabled = False
                if slot.busy:
                    self._retiring.append(slot)
                else:
                    close_now.append(slot)

            self.slots = new_slots
            if self.slots:
                self._next %= len(self.slots)
            else:
                self._next = 0
            self._cond.notify_all()

        for slot in close_now:
            await slot.client.close()
        return True

    # ── In-memory mutation (used when no database is configured) ─────────────

    async def add_key(self, api_key: str, note: str = "") -> dict[str, Any]:
        key_id = key_hash(api_key)
        if self.get_slot(key_id) is not None:
            raise ValueError("Key already exists")
        slot = KeySlot(
            api_key=api_key,
            index=len(self.slots),
            client=self._client_factory(api_key),
            note=note,
            key_id=key_id,
        )
        async with self._cond:
            self.slots.append(slot)
            self._cond.notify_all()
        return slot.public()

    def set_key_enabled(self, key_id: str, enabled: bool) -> dict[str, Any]:
        slot = self.get_slot(key_id)
        if slot is None:
            raise KeyError(f"Unknown key_id: {key_id}")
        slot.enabled = enabled
        return slot.public()

    def set_key_note(self, key_id: str, note: str) -> dict[str, Any]:
        slot = self.get_slot(key_id)
        if slot is None:
            raise KeyError(f"Unknown key_id: {key_id}")
        slot.note = note
        return slot.public()

    async def remove_key(self, key_id: str) -> dict[str, Any]:
        async with self._cond:
            for index, slot in enumerate(self.slots):
                if slot.key_id != key_id:
                    continue
                if slot.busy:
                    raise RuntimeError("Cannot remove a busy key")
                removed = slot.public()
                await slot.client.close()
                self.slots.pop(index)
                for position, remaining in enumerate(self.slots):
                    remaining.index = position
                self._next = 0
                self._cond.notify_all()
                return removed
        raise KeyError(f"Unknown key_id: {key_id}")

    # ── Acquisition ──────────────────────────────────────────────────────────

    def _pick_idle(self) -> KeySlot | None:
        """Round-robin over enabled, idle keys."""
        count = len(self.slots)
        if count == 0:
            return None
        for offset in range(count):
            slot = self.slots[(self._next + offset) % count]
            if slot.enabled and not slot.busy:
                self._next = (self._next + offset + 1) % count
                return slot
        return None

    async def acquire(self, timeout: float | None = None) -> KeyLease:
        """Wait for any enabled key, strictly FIFO.

        Raises `TimeoutError` when no key frees up before the deadline.
        """
        deadline = asyncio.get_running_loop().time() + (timeout or self.timeout)
        waiter = object()
        async with self._cond:
            self._waiters.append(waiter)
            try:
                while True:
                    if self._waiters and self._waiters[0] is waiter:
                        slot = self._pick_idle()
                        if slot is not None:
                            slot.busy = True
                            self._waiters.popleft()
                            return KeyLease(self, slot)
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError("no NovelAI key is currently available")
                    try:
                        await asyncio.wait_for(self._cond.wait(), remaining)
                    except asyncio.TimeoutError as exc:
                        raise TimeoutError("no NovelAI key is currently available") from exc
            except BaseException:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
                self._cond.notify_all()
                raise

    async def release(self, slot: KeySlot) -> None:
        close_client = False
        async with self._cond:
            slot.busy = False
            if slot in self._retiring:
                self._retiring.remove(slot)
                close_client = True
            self._cond.notify_all()
        if close_client:
            await slot.client.close()

    async def close(self) -> None:
        slots = [*self.slots, *self._retiring]
        self.slots = []
        self._retiring = []
        for slot in slots:
            await slot.client.close()


#: Historical aliases so existing imports keep working.
Scheduler = KeyScheduler
Lease = KeyLease
