"""Key pool checks: FIFO order, single in-flight per key, timeout, enable/disable."""
import asyncio
import os
import tempfile

os.environ["BESTNAI_DYNAMIC_POOL"] = "true"
os.environ["BESTNAI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "pool.db")
os.environ["BESTNAI_API_KEYS"] = ""

from app.db import Database  # noqa: E402
from app.key_store import (  # noqa: E402
    active_key_records,
    apply_account_snapshot,
    create_key,
    delete_keys,
    list_key_rows,
    row_to_public,
    set_key_enabled,
)
from app.pool import sync_pool  # noqa: E402
from app.pricing_store import seed_built_in_rules  # noqa: E402
from app.scheduler import KeyScheduler  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f"  <- {detail}"))
    if not ok:
        failures.append(label)


class FakeClient:
    """Stands in for a real upstream client; records close() calls."""

    def __init__(self, api_key):
        self.api_key = api_key
        self.closed = False
        self.snapshot = {}

    async def close(self):
        self.closed = True

    async def get_account_snapshot(self):
        return dict(self.snapshot)


def factory(api_key):
    return FakeClient(api_key)


async def main():
    db = Database(":memory:")
    await db.initialize()
    await seed_built_in_rules(db)

    # ── Pool starts empty ────────────────────────────────────────────────────
    scheduler = KeyScheduler([], timeout=0.2, client_factory=factory)
    await sync_pool(db, scheduler)
    check("empty pool reports byok-only", scheduler.health()["mode"] == "byok-only", scheduler.health())

    try:
        await scheduler.acquire()
        check("empty pool times out", False, "acquire returned")
    except TimeoutError:
        check("empty pool times out", True)

    # ── Add keys ─────────────────────────────────────────────────────────────
    await create_key(db, api_key="pst-" + "a" * 40, note="alpha")
    await create_key(db, api_key="pst-" + "b" * 40, note="beta")
    await create_key(db, api_key="pst-" + "c" * 40, note="gamma")
    await sync_pool(db, scheduler)
    check("three keys in the pool", scheduler.total_keys == 3, scheduler.health())
    check("all three idle", scheduler.idle_keys == 3, scheduler.health())

    # ── Single in-flight per key ─────────────────────────────────────────────
    leases = []
    for _ in range(3):
        leases.append(await scheduler.acquire())
    check("three concurrent leases take three distinct keys",
          len({lease.key_id for lease in leases}) == 3, [lease.key_id[:8] for lease in leases])
    check("pool is fully busy", scheduler.busy_keys == 3 and scheduler.idle_keys == 0, scheduler.health())

    # ── FIFO: queued requests are served in arrival order ────────────────────
    # Each waiter holds its lease until told to release, so ordering is
    # observable rather than racing with an immediate release.
    order = []
    release_events = {"first": asyncio.Event(), "second": asyncio.Event()}

    async def waiter(tag, delay):
        await asyncio.sleep(delay)
        lease = await scheduler.acquire()
        order.append(tag)
        await release_events[tag].wait()
        await lease.release()

    first = asyncio.create_task(waiter("first", 0.0))
    second = asyncio.create_task(waiter("second", 0.05))
    await asyncio.sleep(0.15)
    check("queued requests are counted", scheduler.queued_requests == 2, scheduler.health())

    await leases[0].release()
    await asyncio.sleep(0.05)
    check("head-of-line waiter is served first",
          order == ["first"] and scheduler.queued_requests == 1, f"order={order} queued={scheduler.queued_requests}")

    release_events["first"].set()
    await asyncio.sleep(0.05)
    check("second waiter follows once a key frees",
          order == ["first", "second"], order)

    release_events["second"].set()
    await leases[1].release()
    await leases[2].release()
    await asyncio.gather(first, second)
    check("queue drains", scheduler.queued_requests == 0 and scheduler.busy_keys == 0, scheduler.health())

    # ── Round-robin distribution ─────────────────────────────────────────────
    seen = []
    for _ in range(6):
        lease = await scheduler.acquire()
        seen.append(lease.slot.index)
        await lease.release()
    check("round-robin spreads across all keys", set(seen) == {0, 1, 2}, seen)

    # ── Disable removes a key from scheduling ────────────────────────────────
    rows = await list_key_rows(db)
    beta = next(row for row in rows if row["note"] == "beta")
    await set_key_enabled(db, beta["key_hash"], False)
    await sync_pool(db, scheduler)
    check("disabled key leaves the pool", scheduler.total_keys == 2, scheduler.health())

    disabled_slot = scheduler.get_slot(beta["key_hash"])
    check("disabled key has no slot", disabled_slot is None)

    seen = set()
    for _ in range(6):
        lease = await scheduler.acquire()
        seen.add(lease.slot.index)
        await lease.release()
    check("disabled key is never selected", seen == {0, 1}, seen)

    # ── Re-enable restores it ────────────────────────────────────────────────
    await set_key_enabled(db, beta["key_hash"], True)
    await sync_pool(db, scheduler)
    check("re-enabled key returns to the pool", scheduler.total_keys == 3, scheduler.health())

    # ── Removing a busy key retires it after the lease releases ──────────────
    lease = await scheduler.acquire()
    busy_key_id = lease.key_id
    await delete_keys(db, [busy_key_id])
    await sync_pool(db, scheduler)
    check("busy key is retired, not yanked", scheduler.busy_keys == 1, scheduler.health())
    client = lease.client
    await lease.release()
    check("retired key's client is closed on release", client.closed, "client still open")

    seen = set()
    for _ in range(6):
        acquired = await scheduler.acquire()
        seen.add(acquired.key_id)
        await acquired.release()
    check("retired key is gone from scheduling", busy_key_id not in seen, seen)

    # ── Account information is display-only ──────────────────────────────────
    alpha = next(row for row in await list_key_rows(db) if row["note"] == "alpha")
    await apply_account_snapshot(db, key_hash_value=alpha["key_hash"], snapshot={
        "subscription_tier": 0,
        "subscription_active": True,
        "is_opus": False,
        "total_anlas": 42,
        "account_status": "active",
    })
    public = row_to_public(await db.fetchone("SELECT * FROM nai_keys WHERE key_hash = ?", (alpha["key_hash"],)))
    check("account info is recorded for display",
          public["total_anlas"] == 42 and public["subscription_tier_name"] == "Paper", public)

    # A Paper key with 42 Anlas is still fully schedulable: no tier routing exists.
    lease = await scheduler.acquire()
    check("a Paper key still serves traffic (no tier routing)", lease.key_id == alpha["key_hash"], lease.key_id[:8])
    await lease.release()

    # A failed refresh keeps the last values and never disables the key.
    await apply_account_snapshot(db, key_hash_value=alpha["key_hash"], snapshot=None, error_text="HTTP 402")
    refreshed = await db.fetchone("SELECT * FROM nai_keys WHERE key_hash = ?", (alpha["key_hash"],))
    check("failed refresh keeps the previous snapshot",
          refreshed["total_anlas"] == 42 and refreshed["account_error"] == "HTTP 402", dict(refreshed))
    check("failed refresh does not disable the key", bool(refreshed["enabled"]))

    # ── Reconciliation drops keys deleted out of band ────────────────────────
    remaining = [row["key_hash"] for row in await list_key_rows(db)]
    await delete_keys(db, remaining)
    await sync_pool(db, scheduler)
    check("pool empties after all keys are deleted", scheduler.total_keys == 0, scheduler.health())

    await scheduler.close()
    await db.close()


asyncio.run(main())

print()
if failures:
    print(f"{len(failures)} FAILURE(S): " + ", ".join(failures))
    raise SystemExit(1)
print("all pool checks passed")
