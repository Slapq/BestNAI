"""Shared generation execution: pricing, idempotency, leasing, settlement.

Every public generation surface funnels through this module so pricing,
idempotency, key leasing, and settlement behave identically regardless of which
API dialect the caller used.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .client import NovelAIAPIError, StreamChunk
from .db import Database
from .errors import PlatformError, resolve_upstream_public_error
from .generation import (
    ALIAS_BUDGET_KEY,
    ALIAS_REQUESTED_MODEL_KEY,
    create_job,
    finalize_job_failure,
    finalize_job_success,
    monotonic_ms,
    new_job_id,
    new_request_id,
    normalize_idempotency_key,
    raise_if_duplicate_generation,
    request_fingerprint,
)
from .pricing import PricingRule
from .pricing_store import resolve_generation_pricing, snapshot_for_job
from .scheduler import KeyLease, KeyScheduler


@dataclass
class PreparedGeneration:
    """Everything needed to run one generation after pricing succeeded."""

    native_payload: dict[str, Any]
    job_fields: dict[str, Any]
    rule: PricingRule
    breakdown: dict[str, Any]
    job_id: str
    request_id: str
    entrypoint: str
    idempotency_key: str | None
    fingerprint: str | None
    started_ms: int
    job_row: dict[str, Any] = field(default_factory=dict)

    @property
    def estimated_anlas(self) -> int:
        return int(self.breakdown.get("estimated_anlas") or 0)

    @property
    def cost_gems(self) -> int:
        return int(self.breakdown.get("total_gems") or 0)


async def prepare_generation(
    db: Database,
    *,
    entrypoint: str,
    native_payload: dict[str, Any],
    idempotency_key: str | None = None,
    request_id: str | None = None,
    job_id: str | None = None,
) -> PreparedGeneration:
    """Price a request, enforce idempotency and alias budget, and create the job.

    The legacy `-anlas-N` alias budget is consumed here, after pricing, so the
    private keys never reach the fingerprint or the upstream payload.
    """
    payload = dict(native_payload)
    alias_budget = payload.pop(ALIAS_BUDGET_KEY, None)
    payload.pop(ALIAS_REQUESTED_MODEL_KEY, None)

    key = normalize_idempotency_key(idempotency_key)
    fingerprint = request_fingerprint(payload) if key else None
    await raise_if_duplicate_generation(
        db,
        entrypoint=entrypoint,
        idempotency_key=key,
        fingerprint=fingerprint,
    )

    rule, breakdown, job_fields = await resolve_generation_pricing(db, payload)

    if isinstance(alias_budget, int) and alias_budget >= 0:
        estimated = int(breakdown.get("estimated_anlas") or 0)
        if estimated > alias_budget:
            raise PlatformError(
                "TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED",
                "Estimated Anlas exceeds the model alias limit",
                402,
                {
                    "estimated_anlas": estimated,
                    "max_anlas": alias_budget,
                    "resolved_model": job_fields["model"],
                },
            )

    resolved_request_id = request_id or new_request_id()
    resolved_job_id = job_id or new_job_id()
    snapshot = snapshot_for_job(
        job_fields=job_fields,
        breakdown=breakdown,
        rule=rule,
    )
    job_row = await create_job(
        db,
        job_id=resolved_job_id,
        request_id=resolved_request_id,
        entrypoint=entrypoint,
        model=job_fields["model"],
        action=job_fields["action"],
        cost_gems=breakdown.get("total_gems", 0),
        estimated_anlas=breakdown.get("estimated_anlas") or 0,
        pricing_snapshot=snapshot,
        idempotency_key=key,
        fingerprint=fingerprint,
    )
    return PreparedGeneration(
        native_payload=payload,
        job_fields=job_fields,
        rule=rule,
        breakdown=breakdown,
        job_id=resolved_job_id,
        request_id=resolved_request_id,
        entrypoint=entrypoint,
        idempotency_key=key,
        fingerprint=fingerprint,
        started_ms=monotonic_ms(),
        job_row=job_row,
    )


async def acquire_lease(
    scheduler: KeyScheduler,
    *,
    timeout: float | None = None,
) -> KeyLease:
    """Lease any available key.

    A `TimeoutError` becomes the public queue error: HTTP 503
    `UPSTREAM_UNAVAILABLE`.  That is the only queue failure BestNAI exposes.
    """
    try:
        return await scheduler.acquire(timeout)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise queue_error() from exc


def queue_error() -> PlatformError:
    """The public error for "no key became available before the deadline"."""
    return PlatformError("UPSTREAM_UNAVAILABLE", None, 503)


def upstream_error(exc: NovelAIAPIError, *, language: str = "en") -> PlatformError:
    """Translate an upstream failure into the public platform error."""
    status, code, message = resolve_upstream_public_error(
        exc.error_code,
        exc.status_code,
        language=language,
    )
    return PlatformError(code, message, status, {"upstream_status": exc.status_code})


async def fail_job(db: Database, prepared: PreparedGeneration, message: str) -> None:
    """Mark a charged job as failed."""
    await finalize_job_failure(
        db,
        job_id=prepared.job_id,
        message=message,
        duration_ms=monotonic_ms() - prepared.started_ms,
    )


async def succeed_job(db: Database, prepared: PreparedGeneration, image_count: int) -> None:
    """Mark a charged job as succeeded."""
    await finalize_job_success(
        db,
        job_id=prepared.job_id,
        image_count=image_count,
        duration_ms=monotonic_ms() - prepared.started_ms,
    )


# ── Non-streaming execution ───────────────────────────────────────────────────


async def run_generation(
    db: Database,
    scheduler: KeyScheduler,
    prepared: PreparedGeneration,
    *,
    language: str = "en",
) -> list[Any]:
    """Run one non-streaming generation, settling the job on every outcome.

    A job must never be left `charged`: lease acquisition, the upstream call, and
    settlement failures all mark it failed.
    """
    lease: KeyLease | None = None
    try:
        lease = await acquire_lease(scheduler)
        images = await lease.client.generate_image(prepared.native_payload)
    except PlatformError as exc:
        await fail_job(db, prepared, exc.code)
        raise
    except NovelAIAPIError as exc:
        await fail_job(db, prepared, str(exc))
        raise upstream_error(exc, language=language) from exc
    except BaseException as exc:
        await fail_job(db, prepared, str(exc))
        raise
    else:
        await succeed_job(db, prepared, len(images))
        return images
    finally:
        if lease is not None:
            await lease.release()


# ── Streaming execution ───────────────────────────────────────────────────────


async def stream_generation(
    db: Database,
    scheduler: KeyScheduler,
    prepared: PreparedGeneration,
    *,
    language: str = "en",
) -> AsyncIterator[StreamChunk]:
    """Run one streaming generation, yielding upstream chunks.

    The lease is held for the whole stream.  Failures surface as `PlatformError`
    raised from inside the generator so each API dialect can render its own
    envelope.
    """
    lease: KeyLease | None = None
    try:
        lease = await acquire_lease(scheduler)
        async for chunk in lease.client.generate_image_stream(prepared.native_payload):
            yield chunk
        await succeed_job(db, prepared, 1)
    except PlatformError as exc:
        await fail_job(db, prepared, exc.code)
        raise
    except NovelAIAPIError as exc:
        await fail_job(db, prepared, str(exc))
        raise upstream_error(exc, language=language) from exc
    except BaseException as exc:
        await fail_job(db, prepared, str(exc))
        raise
    finally:
        if lease is not None:
            await lease.release()


async def open_raw_stream(
    db: Database,
    scheduler: KeyScheduler,
    prepared: PreparedGeneration,
    *,
    language: str = "en",
) -> tuple[Any, KeyLease]:
    """Open the raw upstream MessagePack stream, returning `(response, lease)`.

    The caller owns the response and must release the lease after consuming it.
    """
    lease: KeyLease | None = None
    try:
        lease = await acquire_lease(scheduler)
        upstream = await lease.client.open_raw_generate_stream(prepared.native_payload)
    except PlatformError as exc:
        await fail_job(db, prepared, exc.code)
        if lease is not None:
            await lease.release()
        raise
    except NovelAIAPIError as exc:
        await fail_job(db, prepared, str(exc))
        if lease is not None:
            await lease.release()
        raise upstream_error(exc, language=language) from exc
    except BaseException:
        await fail_job(db, prepared, "stream setup failed")
        if lease is not None:
            await lease.release()
        raise
    assert lease is not None
    return upstream, lease


# ── Non-generation (tool) execution ───────────────────────────────────────────


async def run_tool(
    db: Database,
    scheduler: KeyScheduler,
    *,
    operation: str,
    call: Any,
    language: str = "en",
) -> Any:
    """Run a non-generation upstream operation under a leased key.

    Tool calls are not priced and do not create generation jobs.
    """
    lease = await acquire_lease(scheduler)
    async with lease:
        try:
            return await call(lease)
        except NovelAIAPIError as exc:
            raise upstream_error(exc, language=language) from exc
