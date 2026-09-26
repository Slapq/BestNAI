"""Durable key pool storage.

BestNAI keeps a simple pool of NovelAI keys.  Each key is either enabled or
disabled, has a note, and may carry the last observed upstream account
information for display.

That account information is **informational only**.  BestNAI does not route by
subscription tier, free allowance, or key health; it does not quarantine keys
automatically.  Scheduling is plain FIFO with one in-flight request per key.
"""
from __future__ import annotations

from typing import Any

from .db import Database, dump_json, utc_now
from .errors import PlatformError
from .key_policy import key_hash, mask_key

#: Columns copied verbatim from an upstream account snapshot.
SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "subscription_tier",
    "subscription_active",
    "subscription_expires_at",
    "fixed_anlas",
    "purchased_anlas",
    "total_anlas",
    "is_opus",
    "is_grace_period",
    "email_verified",
    "trial_activated",
    "trial_images_left",
    "ban_status",
    "account_status",
)

_BOOL_COLUMNS = frozenset({
    "subscription_active",
    "is_opus",
    "is_grace_period",
    "email_verified",
    "trial_activated",
})

_INT_COLUMNS = frozenset({
    "subscription_tier",
    "fixed_anlas",
    "purchased_anlas",
    "total_anlas",
    "trial_images_left",
})

_TIER_NAMES = {0: "Paper", 1: "Tablet", 2: "Scroll", 3: "Opus"}


def _coerce(column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in _BOOL_COLUMNS:
        return 1 if value else 0
    if column in _INT_COLUMNS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return value


def row_to_public(row: dict[str, Any]) -> dict[str, Any]:
    """Project a stored key row into the admin-facing representation."""
    tier = row.get("subscription_tier")
    return {
        "key_id": row.get("key_hash"),
        "id": row.get("id"),
        "masked_key": row.get("masked_key"),
        "note": row.get("note") or "",
        "enabled": bool(row.get("enabled")),
        "subscription_tier": tier,
        "subscription_tier_name": _TIER_NAMES.get(int(tier)) if isinstance(tier, int) else None,
        "subscription_active": (
            bool(row.get("subscription_active"))
            if row.get("subscription_active") is not None
            else None
        ),
        "subscription_expires_at": row.get("subscription_expires_at"),
        "fixed_anlas": row.get("fixed_anlas"),
        "purchased_anlas": row.get("purchased_anlas"),
        "total_anlas": row.get("total_anlas"),
        "is_opus": bool(row.get("is_opus")) if row.get("is_opus") is not None else None,
        "is_grace_period": (
            bool(row.get("is_grace_period")) if row.get("is_grace_period") is not None else None
        ),
        "email_verified": row.get("email_verified"),
        "trial_activated": row.get("trial_activated"),
        "trial_images_left": row.get("trial_images_left"),
        "ban_status": row.get("ban_status") or "",
        "account_status": row.get("account_status") or "unknown",
        "account_checked_at": row.get("account_checked_at"),
        "account_error": row.get("account_error") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ── Reads ─────────────────────────────────────────────────────────────────────


async def list_key_rows(
    db: Database,
    *,
    enabled: bool | None = None,
    keyword: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if enabled is not None:
        clauses.append("enabled = ?")
        params.append(1 if enabled else 0)
    if keyword:
        clauses.append("(note LIKE ? OR masked_key LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit), 500)))
    return await db.fetchall(
        f"SELECT * FROM nai_keys {where} ORDER BY id ASC LIMIT ?",
        tuple(params),
    )


async def active_key_records(db: Database) -> list[dict[str, Any]]:
    """Enabled keys ordered by id — the scheduler's authoritative input."""
    rows = await db.fetchall("SELECT * FROM nai_keys WHERE enabled = 1 ORDER BY id ASC")
    return [
        {
            "key_id": row["key_hash"],
            "api_key": row["api_key"],
            "note": row.get("note") or "",
        }
        for row in rows
    ]


async def get_key_row(db: Database, key_id: str) -> dict[str, Any] | None:
    return await db.fetchone(
        "SELECT * FROM nai_keys WHERE key_hash = ? OR id = ? LIMIT 1",
        (key_id, key_id if str(key_id).isdigit() else -1),
    )


async def get_key_by_hash(db: Database, key_hash_value: str) -> dict[str, Any] | None:
    return await db.fetchone("SELECT * FROM nai_keys WHERE key_hash = ?", (key_hash_value,))


# ── Writes ────────────────────────────────────────────────────────────────────


async def create_key(db: Database, *, api_key: str, note: str = "") -> dict[str, Any]:
    key_id = key_hash(api_key)
    if await get_key_by_hash(db, key_id) is not None:
        raise PlatformError("DUPLICATE_GENERATION_REQUEST", "Key already exists", 409)
    now = utc_now()
    await db.execute(
        """
        INSERT INTO nai_keys (
            key_hash, api_key, masked_key, note, enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (key_id, api_key, mask_key(api_key), note, now, now),
    )
    row = await get_key_by_hash(db, key_id)
    assert row is not None
    return row


async def update_key_note(db: Database, key_id: str, note: str) -> dict[str, Any]:
    row = await get_key_row(db, key_id)
    if row is None:
        raise PlatformError("NOT_FOUND", "Key not found", 404)
    await db.execute(
        "UPDATE nai_keys SET note = ?, updated_at = ? WHERE key_hash = ?",
        (note, utc_now(), row["key_hash"]),
    )
    updated = await get_key_by_hash(db, row["key_hash"])
    assert updated is not None
    return updated


async def set_key_enabled(db: Database, key_id: str, enabled: bool) -> dict[str, Any]:
    row = await get_key_row(db, key_id)
    if row is None:
        raise PlatformError("NOT_FOUND", "Key not found", 404)
    await db.execute(
        "UPDATE nai_keys SET enabled = ?, updated_at = ? WHERE key_hash = ?",
        (1 if enabled else 0, utc_now(), row["key_hash"]),
    )
    updated = await get_key_by_hash(db, row["key_hash"])
    assert updated is not None
    return updated


async def delete_keys(db: Database, key_ids: list[str]) -> int:
    deleted = 0
    for key_id in key_ids:
        row = await get_key_row(db, key_id)
        if row is None:
            continue
        await db.execute("DELETE FROM nai_keys WHERE key_hash = ?", (row["key_hash"],))
        deleted += 1
    return deleted


async def apply_account_snapshot(
    db: Database,
    *,
    key_hash_value: str,
    snapshot: dict[str, Any] | None,
    error_text: str = "",
) -> dict[str, Any] | None:
    """Record the last observed upstream account state for display.

    A failed refresh keeps the previous successful values and only records the
    error; it never disables the key.
    """
    row = await get_key_by_hash(db, key_hash_value)
    if row is None:
        return None
    now = utc_now()

    if snapshot is None:
        await db.execute(
            "UPDATE nai_keys SET account_checked_at = ?, account_error = ?, updated_at = ? "
            "WHERE key_hash = ?",
            (now, (error_text or "")[:500], now, key_hash_value),
        )
        return await get_key_by_hash(db, key_hash_value)

    values: dict[str, Any] = {}
    for column in SNAPSHOT_COLUMNS:
        if column in snapshot:
            values[column] = _coerce(column, snapshot.get(column))
    values.setdefault("account_status", "active")
    if values.get("ban_status"):
        values["ban_status"] = str(values["ban_status"])[:64]
    values["account_status"] = str(values.get("account_status") or "unknown")[:24]
    values["account_checked_at"] = now
    values["account_error"] = str(snapshot.get("information_error") or "")[:500]
    values["updated_at"] = now

    assignments = [f"{column} = ?" for column in values]
    params = list(values.values())
    params.append(key_hash_value)
    await db.execute(
        f"UPDATE nai_keys SET {', '.join(assignments)} WHERE key_hash = ?",
        tuple(params),
    )
    return await get_key_by_hash(db, key_hash_value)


__all__ = [
    "SNAPSHOT_COLUMNS",
    "active_key_records",
    "apply_account_snapshot",
    "create_key",
    "delete_keys",
    "get_key_by_hash",
    "get_key_row",
    "list_key_rows",
    "row_to_public",
    "set_key_enabled",
    "update_key_note",
    "dump_json",
]
