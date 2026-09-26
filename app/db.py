"""SQLite persistence for the dynamic key pool, pricing rules, and idempotency.

BestNAI keeps *runtime* scheduling state in memory (see `scheduler.py`) but
persists the durable facts that must survive a restart:

* the NAI key pool and its account snapshots / availability policy
* pricing rules and site options
* idempotency records for generation requests

Only one writer touches the file, guarded by an `asyncio.Lock`, with WAL enabled
so concurrent readers are not blocked.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

SCHEMA_VERSION = 1

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nai_keys (
        id                       INTEGER PRIMARY KEY AUTOINCREMENT,
        key_hash                 TEXT    NOT NULL UNIQUE,
        api_key                  TEXT    NOT NULL,
        masked_key               TEXT    NOT NULL DEFAULT '',
        note                     TEXT    NOT NULL DEFAULT '',
        enabled                  INTEGER NOT NULL DEFAULT 1,
        subscription_tier        INTEGER,
        subscription_active      INTEGER,
        subscription_expires_at  TEXT,
        fixed_anlas              INTEGER,
        purchased_anlas          INTEGER,
        total_anlas              INTEGER,
        is_opus                  INTEGER,
        is_grace_period          INTEGER,
        email_verified           INTEGER,
        trial_activated          INTEGER,
        trial_images_left        INTEGER,
        ban_status               TEXT    NOT NULL DEFAULT '',
        account_status           TEXT    NOT NULL DEFAULT 'unknown',
        account_checked_at       TEXT,
        account_error            TEXT    NOT NULL DEFAULT '',
        created_at               TEXT    NOT NULL,
        updated_at               TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nai_keys_enabled ON nai_keys (enabled)",
    """
    CREATE TABLE IF NOT EXISTS pricing_rules (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        model          TEXT    NOT NULL,
        action         TEXT    NOT NULL DEFAULT 'generate',
        size_bucket    TEXT    NOT NULL DEFAULT 'default',
        min_pixels     INTEGER NOT NULL DEFAULT 0,
        max_pixels     INTEGER NOT NULL DEFAULT 3145728,
        min_steps      INTEGER NOT NULL DEFAULT 1,
        max_steps      INTEGER NOT NULL DEFAULT 50,
        min_images     INTEGER NOT NULL DEFAULT 1,
        max_images     INTEGER NOT NULL DEFAULT 1,
        base_gems      INTEGER NOT NULL DEFAULT 0,
        enabled        INTEGER NOT NULL DEFAULT 1,
        priority       INTEGER NOT NULL DEFAULT 100,
        allowed_sizes  TEXT    NOT NULL DEFAULT '[]',
        feature_config TEXT    NOT NULL DEFAULT '{}',
        built_in       INTEGER NOT NULL DEFAULT 0,
        created_at     TEXT    NOT NULL,
        updated_at     TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pricing_rules_model_action ON pricing_rules (model, action)",
    "CREATE INDEX IF NOT EXISTS ix_pricing_rules_enabled ON pricing_rules (enabled)",
    """
    CREATE TABLE IF NOT EXISTS site_options (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS generation_jobs (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id             TEXT    NOT NULL UNIQUE,
        request_id         TEXT    NOT NULL DEFAULT '',
        entrypoint         TEXT    NOT NULL DEFAULT '',
        idempotency_key    TEXT,
        request_fingerprint TEXT,
        model              TEXT    NOT NULL DEFAULT '',
        action             TEXT    NOT NULL DEFAULT '',
        status             TEXT    NOT NULL DEFAULT 'charged',
        cost_gems          INTEGER NOT NULL DEFAULT 0,
        estimated_anlas    INTEGER NOT NULL DEFAULT 0,
        pricing_snapshot   TEXT    NOT NULL DEFAULT '{}',
        error_message      TEXT    NOT NULL DEFAULT '',
        image_count        INTEGER NOT NULL DEFAULT 0,
        duration_ms        INTEGER,
        created_at         TEXT    NOT NULL,
        updated_at         TEXT    NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_generation_jobs_entrypoint_idempotency "
    "ON generation_jobs (entrypoint, idempotency_key) WHERE idempotency_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_generation_jobs_created_at ON generation_jobs (created_at)",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_database_path() -> Path:
    override = os.getenv("BESTNAI_DB_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "data" / "bestnai.db"


class Database:
    """A tiny async SQLite wrapper with serialized writes."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_database_path()
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def connect(self) -> None:
        if self._conn is not None:
            return
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(self.path))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn

    async def initialize(self) -> None:
        await self.connect()
        async with self._write_lock:
            for statement in SCHEMA_STATEMENTS:
                await self.connection.execute(statement)
            await self.connection.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
            await self.connection.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ── Query helpers ────────────────────────────────────────────────────────

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a write statement and return `lastrowid`."""
        async with self._write_lock:
            cursor = await self.connection.execute(sql, params)
            await self.connection.commit()
            return cursor.lastrowid or 0

    async def execute_many(self, sql: str, params: Iterable[Sequence[Any]]) -> None:
        async with self._write_lock:
            await self.connection.executemany(sql, list(params))
            await self.connection.commit()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        cursor = await self.connection.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]


def parse_json_object(raw: object, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return dict(default or {})
        return parsed if isinstance(parsed, dict) else dict(default or {})
    return dict(default or {})


def parse_json_list(raw: object) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
