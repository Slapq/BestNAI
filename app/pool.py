"""Key pool synchronization and account inspection.

BestNAI's pool is a thin projection of the durable enabled-key set.  This module
keeps the in-memory scheduler aligned with the database and can refresh a key's
upstream account information on demand.

Account information is display-only.  It never influences scheduling, and no
failure disables a key automatically — an operator decides that.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .client import NovelAIAPIError
from .db import Database
from .key_store import active_key_records, apply_account_snapshot, get_key_by_hash
from .scheduler import KeyScheduler


def pool_anlas(scheduler: KeyScheduler) -> int:
    """Sum of last-known Anlas across enabled keys.

    Display only: it is what a Launcher renders as "Anlas", and it never
    influences scheduling or pricing.
    """
    return sum(
        int(getattr(slot, "total_anlas", 0) or 0) for slot in scheduler.slots if slot.enabled
    )


async def sync_pool(db: Database, scheduler: KeyScheduler) -> dict[str, Any]:
    """Reconcile the scheduler against the durable, enabled key set."""
    records = await active_key_records(db)
    await scheduler.replace_keys(records)
    return {
        "synced": True,
        "total_keys": scheduler.total_keys,
        "busy_keys": scheduler.busy_keys,
    }


async def key_sync_loop(
    db: Database,
    scheduler: KeyScheduler,
    *,
    interval_seconds: float = 30.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Periodically reconcile the pool so out-of-band edits take effect."""
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            await sync_pool(db, scheduler)
        except Exception:
            # A transient database problem must not kill the loop.
            pass
        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(), timeout=max(5.0, interval_seconds))
                return
            await asyncio.sleep(max(5.0, interval_seconds))
        except asyncio.TimeoutError:
            continue


async def refresh_account_snapshot(
    db: Database,
    scheduler: KeyScheduler | None,
    key_hash_value: str,
) -> dict[str, Any] | None:
    """Fetch and store a key's upstream account information for display."""
    snapshot: dict[str, Any] | None = None
    error_text = ""

    slot = scheduler.get_slot(key_hash_value) if scheduler is not None else None
    if slot is not None:
        try:
            snapshot = await slot.client.get_account_snapshot()
        except NovelAIAPIError as exc:
            error_text = f"{exc}\n{exc.body}"[:4000]
        except Exception as exc:  # network/SDK surprises must not escape
            error_text = str(exc)[:4000]

    return await apply_account_snapshot(
        db,
        key_hash_value=key_hash_value,
        snapshot=snapshot,
        error_text=error_text,
    )


async def refresh_key_by_client(
    db: Database,
    key_hash_value: str,
    client: Any,
) -> dict[str, Any] | None:
    """Refresh a key's account information using a client not yet in the pool."""
    snapshot: dict[str, Any] | None = None
    error_text = ""
    try:
        snapshot = await client.get_account_snapshot()
    except NovelAIAPIError as exc:
        error_text = f"{exc}\n{exc.body}"[:4000]
    except Exception as exc:
        error_text = str(exc)[:4000]
    return await apply_account_snapshot(
        db,
        key_hash_value=key_hash_value,
        snapshot=snapshot,
        error_text=error_text,
    )
