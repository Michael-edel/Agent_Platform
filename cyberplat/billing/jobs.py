"""Billing jobs processor (production-grade retry/locking, test-friendly).

- Supports retry policy with max_attempts + exponential backoff with jitter.
- Protects from parallel processing:
  - Postgres: SELECT ... FOR UPDATE SKIP LOCKED
  - SQLite: best-effort locking via locked_at/locked_by.
- Safe handling of provider_ref: never create a new payment if provider_ref already exists.
"""

from __future__ import annotations

import os
import random
import socket
from dataclasses import dataclass
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import BillingJob, UsageInvoice


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _is_dry_run() -> bool:
    return os.getenv("BILLING_DRY_RUN", "true").strip().lower() in {"1", "true", "yes"}

def _worker_id() -> str:
    return os.getenv("BILLING_WORKER_ID", "").strip() or os.getenv("HOSTNAME", "").strip() or socket.gethostname()


def compute_next_attempt_at(attempt_count: int, base_seconds: int = 60, max_seconds: int = 3600) -> datetime:
    """
    Exponential backoff with jitter +/-10%:
      delay = min(max_seconds, base_seconds * 2^(attempt_count-1))
    """
    attempt = max(int(attempt_count), 1)
    delay = min(int(max_seconds), int(base_seconds) * (2 ** (attempt - 1)))
    jitter = random.uniform(-0.1, 0.1) * float(delay)
    delay_with_jitter = max(0.0, float(delay) + jitter)
    return datetime.now(timezone.utc) + timedelta(seconds=delay_with_jitter)


def _map_stripe_intent_status_to_payment_status(status: str) -> str:
    s = (status or "").strip().lower()
    if s == "succeeded":
        return "paid"
    if s in {"canceled", "requires_payment_method"}:
        return "failed"
    return "processing"


@dataclass(frozen=True)
class DueJob:
    id: str
    tenant_id: str
    invoice_id: str
    provider: str
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: Optional[str]

JobOutcome = Literal["succeeded", "failed", "retried", "skipped"]


def _eligible_query(now: str):
    return (
        select(BillingJob)
        .where(BillingJob.status.in_(["pending", "pending_retry", "failed"]))
        .where(or_(BillingJob.next_attempt_at.is_(None), BillingJob.next_attempt_at <= now))
        .where(BillingJob.attempt_count < BillingJob.max_attempts)
        .where(BillingJob.locked_at.is_(None))
        .order_by(BillingJob.next_attempt_at.is_not(None), BillingJob.created_at)
    )


def claim_due_jobs(now: str, limit: int = 50, *, worker_id: Optional[str] = None) -> List[DueJob]:
    """
    Claim a batch of due jobs safely.
    - Postgres: uses SKIP LOCKED.
    - SQLite: best-effort using locked_at/locked_by in an atomic UPDATE.
    """
    engine = get_engine()
    dialect = engine.dialect.name
    worker = (worker_id or "").strip() or _worker_id()
    claimed: List[DueJob] = []

    with Session(engine) as session:
        if dialect == "postgresql":
            jobs = (
                session.execute(_eligible_query(now).with_for_update(skip_locked=True).limit(limit))
                .scalars()
                .all()
            )
            for job in jobs:
                job.status = "processing"
                job.processing_started_at = job.processing_started_at or now
                job.updated_at = now
                job.locked_at = now
                job.locked_by = worker
                job.last_attempt_at = now
                claimed.append(
                    DueJob(
                        id=str(job.id),
                        tenant_id=str(job.tenant_id),
                        invoice_id=str(job.invoice_id),
                        provider=str(job.provider or ""),
                        status=str(job.status),
                        attempt_count=int(job.attempt_count or 0),
                        max_attempts=int(job.max_attempts or 0),
                        next_attempt_at=job.next_attempt_at,
                    )
                )
            session.commit()
            return claimed

        # SQLite / others: claim one-by-one with atomic UPDATE guarded by locked_at IS NULL.
        rows = session.execute(_eligible_query(now).with_only_columns(BillingJob.id).limit(limit)).all()
        for (job_id,) in rows:
            res = session.execute(
                update(BillingJob)
                .where(BillingJob.id == job_id)
                .where(BillingJob.locked_at.is_(None))
                .where(BillingJob.status.in_(["pending", "pending_retry", "failed"]))
                .values(
                    status="processing",
                    processing_started_at=now,
                    updated_at=now,
                    locked_at=now,
                    locked_by=worker,
                    last_attempt_at=now,
                )
            )
            session.commit()
            if not (getattr(res, "rowcount", 0) and getattr(res, "rowcount", 0) > 0):
                continue
            job = session.execute(select(BillingJob).where(BillingJob.id == job_id)).scalar_one()
            claimed.append(
                DueJob(
                    id=str(job.id),
                    tenant_id=str(job.tenant_id),
                    invoice_id=str(job.invoice_id),
                    provider=str(job.provider or ""),
                    status=str(job.status),
                    attempt_count=int(job.attempt_count or 0),
                    max_attempts=int(job.max_attempts or 0),
                    next_attempt_at=job.next_attempt_at,
                )
            )
        return claimed


def process_job(job: DueJob, now: str) -> JobOutcome:
    engine = get_engine()
    with Session(engine) as session:
        t0 = time.monotonic()
        db_job = session.execute(select(BillingJob).where(BillingJob.id == job.id)).scalar_one_or_none()
        if not db_job:
            return "skipped"
        # idempotency: if already terminal, do nothing
        if db_job.status in {"succeeded"}:
            return "skipped"

        if _is_dry_run():
            # Mark succeeded and mark invoice paid
            session.execute(
                update(BillingJob)
                .where(BillingJob.id == job.id)
                .where(BillingJob.status == "processing")
                .values(
                    status="succeeded",
                    finished_at=now,
                    updated_at=now,
                    locked_at=None,
                    locked_by=None,
                    last_error_code=None,
                    last_error_message=None,
                    next_attempt_at=None,
                )
            )
            inv_row = session.execute(
                select(UsageInvoice.finalized_at, UsageInvoice.paid_at)
                .where(UsageInvoice.id == job.invoice_id)
                .where(UsageInvoice.tenant_id == job.tenant_id)
                .limit(1)
            ).fetchone()
            finalized_at = inv_row[0] if inv_row else None
            paid_was_none = (inv_row[1] if inv_row else None) in (None, "")
            session.execute(
                update(UsageInvoice)
                .where(UsageInvoice.id == job.invoice_id)
                .where(UsageInvoice.tenant_id == job.tenant_id)
                .where(UsageInvoice.payment_status != "paid")
                .values(
                    payment_status="paid",
                    payment_status_updated_at=now,
                    paid_at=case((UsageInvoice.paid_at.is_(None), now), else_=UsageInvoice.paid_at),
                )
            )
            session.commit()
            # Observe time-to-paid once (best-effort)
            if paid_was_none:
                try:
                    if finalized_at:
                        dt_final = datetime.fromisoformat(str(finalized_at))
                        if dt_final.tzinfo is None:
                            dt_final = dt_final.replace(tzinfo=timezone.utc)
                        dt_now = datetime.fromisoformat(str(now))
                        if dt_now.tzinfo is None:
                            dt_now = dt_now.replace(tzinfo=timezone.utc)
                        from cyberplat.billing.metrics import observe_invoice_time_to_paid

                        observe_invoice_time_to_paid(max(0.0, (dt_now - dt_final).total_seconds()))
                except Exception:
                    pass
            try:
                from cyberplat.billing.metrics import observe_job_duration

                observe_job_duration(str(getattr(db_job, "locked_by", "") or "unknown"), str(getattr(db_job, "provider", "") or ""), time.monotonic() - t0)
            except Exception:
                pass
            return "succeeded"

        inv = session.execute(
            select(UsageInvoice).where(UsageInvoice.id == job.invoice_id, UsageInvoice.tenant_id == job.tenant_id)
        ).scalar_one_or_none()
        if not inv:
            # Nothing to do - fail attempt.
            session.execute(
                update(BillingJob)
                .where(BillingJob.id == job.id)
                .where(BillingJob.status == "processing")
                .values(
                    status="failed",
                    finished_at=now,
                    updated_at=now,
                    locked_at=None,
                    locked_by=None,
                    last_error_code="invoice_not_found",
                    last_error_message="usage invoice not found",
                    next_attempt_at=None,
                )
            )
            session.commit()
            try:
                from cyberplat.billing.metrics import inc_job_failure_reason

                inc_job_failure_reason(str(job.provider or "unknown"), "invoice_not_found", 1)
            except Exception:
                pass
            try:
                from cyberplat.billing.metrics import observe_job_duration

                observe_job_duration(str(getattr(db_job, "locked_by", "") or "unknown"), str(getattr(db_job, "provider", "") or ""), time.monotonic() - t0)
            except Exception:
                pass
            return "failed"

        provider_name = (job.provider or "").strip().lower()
        # If provider_ref already exists, never create another payment.
        if db_job.provider_ref:
            if provider_name == "stripe":
                try:
                    import stripe  # type: ignore

                    key = os.getenv("STRIPE_API_KEY", "").strip() or os.getenv("STRIPE_SECRET_KEY", "").strip()
                    if key:
                        stripe.api_key = key
                        intent = stripe.PaymentIntent.retrieve(str(db_job.provider_ref))
                        intent_status = str(getattr(intent, "status", "") or "")
                        payment_status = _map_stripe_intent_status_to_payment_status(intent_status)
                        try:
                            from cyberplat.billing.metrics import inc_provider_refresh

                            inc_provider_refresh(str(getattr(db_job, "locked_by", "") or "unknown"), "stripe", "ok", 1)
                        except Exception:
                            pass
                        session.execute(
                            update(UsageInvoice)
                            .where(UsageInvoice.id == inv.id)
                            .where(UsageInvoice.tenant_id == inv.tenant_id)
                            .where(UsageInvoice.payment_status != payment_status)
                            .values(
                                **(
                                    {
                                        "payment_status": payment_status,
                                        "payment_status_updated_at": now,
                                    }
                                    | (
                                        {"paid_at": case((UsageInvoice.paid_at.is_(None), now), else_=UsageInvoice.paid_at)}
                                        if payment_status == "paid"
                                        else {}
                                    )
                                    | (
                                        {
                                            "failed_at": case(
                                                (UsageInvoice.failed_at.is_(None), now),
                                                else_=UsageInvoice.failed_at,
                                            )
                                        }
                                        if payment_status == "failed"
                                        else {}
                                    )
                                )
                            )
                        )
                        session.execute(
                            update(BillingJob)
                            .where(BillingJob.id == db_job.id)
                            .values(
                                status="succeeded",
                                finished_at=now,
                                updated_at=now,
                                locked_at=None,
                                locked_by=None,
                                next_attempt_at=None,
                            )
                        )
                        session.commit()
                        if payment_status == "paid" and getattr(inv, "paid_at", None) in (None, ""):
                            try:
                                finalized_at = getattr(inv, "finalized_at", None)
                                if finalized_at:
                                    dt_final = datetime.fromisoformat(str(finalized_at))
                                    if dt_final.tzinfo is None:
                                        dt_final = dt_final.replace(tzinfo=timezone.utc)
                                    dt_now = datetime.fromisoformat(str(now))
                                    if dt_now.tzinfo is None:
                                        dt_now = dt_now.replace(tzinfo=timezone.utc)
                                    from cyberplat.billing.metrics import observe_invoice_time_to_paid

                                    observe_invoice_time_to_paid(max(0.0, (dt_now - dt_final).total_seconds()))
                            except Exception:
                                pass
                        try:
                            from cyberplat.billing.metrics import observe_job_duration

                            observe_job_duration(str(getattr(db_job, "locked_by", "") or "unknown"), "stripe", time.monotonic() - t0)
                        except Exception:
                            pass
                        return "skipped"
                    else:
                        try:
                            from cyberplat.billing.metrics import inc_provider_refresh

                            inc_provider_refresh(str(getattr(db_job, "locked_by", "") or "unknown"), "stripe", "skipped", 1)
                        except Exception:
                            pass
                except Exception:
                    try:
                        from cyberplat.billing.metrics import inc_provider_refresh

                        inc_provider_refresh(str(getattr(db_job, "locked_by", "") or "unknown"), "stripe", "error", 1)
                    except Exception:
                        pass
                    # Fall through to retry scheduling (but never create new payment).
                    pass

            session.execute(
                update(BillingJob)
                .where(BillingJob.id == db_job.id)
                .values(
                    status="succeeded",
                    finished_at=now,
                    updated_at=now,
                    locked_at=None,
                    locked_by=None,
                    next_attempt_at=None,
                )
            )
            session.commit()
            try:
                from cyberplat.billing.metrics import observe_job_duration

                observe_job_duration(str(getattr(db_job, "locked_by", "") or "unknown"), str(getattr(db_job, "provider", "") or ""), time.monotonic() - t0)
            except Exception:
                pass
            return "skipped"

        # Import inside to keep tests monkeypatch-friendly
        from cyberplat.billing.providers.registry import get_provider

        provider = get_provider(provider_name)
        if not provider:
            session.execute(
                update(BillingJob)
                .where(BillingJob.id == job.id)
                .where(BillingJob.status == "processing")
                .values(
                    status="pending_retry",
                    finished_at=now,
                    updated_at=now,
                    locked_at=None,
                    locked_by=None,
                    last_error_code="unknown_provider",
                    last_error_message=f"unknown provider: {provider_name}",
                    next_attempt_at=compute_next_attempt_at(int(db_job.attempt_count or 0) + 1).isoformat(),
                    attempt_count=BillingJob.attempt_count + 1,
                )
            )
            session.commit()
            try:
                from cyberplat.billing.metrics import observe_job_duration

                observe_job_duration(str(getattr(db_job, "locked_by", "") or "unknown"), str(getattr(db_job, "provider", "") or ""), time.monotonic() - t0)
            except Exception:
                pass
            return "retried"

        try:
            result = provider.create_payment(inv)
        except NotImplementedError as e:
            err_code = "not_implemented"
            err_msg = str(e)[:200]
            result = None
        except Exception as e:
            err_code = "provider_error"
            err_msg = str(e)[:200]
            result = None

        if result is None:
            # Failure -> increment attempt_count and schedule retry or fail permanently.
            current_attempt = int(db_job.attempt_count or 0) + 1
            max_attempts = int(db_job.max_attempts or 5)
            if current_attempt >= max_attempts:
                session.execute(
                    update(BillingJob)
                    .where(BillingJob.id == db_job.id)
                    .values(
                        status="failed",
                        attempt_count=current_attempt,
                        finished_at=now,
                        updated_at=now,
                        locked_at=None,
                        locked_by=None,
                        next_attempt_at=None,
                        last_error_code=err_code,
                        last_error_message=err_msg,
                    )
                )
                session.execute(
                    update(UsageInvoice)
                    .where(UsageInvoice.id == inv.id)
                    .where(UsageInvoice.tenant_id == inv.tenant_id)
                    .where(UsageInvoice.payment_status != "failed")
                    .values(
                        payment_status="failed",
                        payment_status_updated_at=now,
                        failed_at=case((UsageInvoice.failed_at.is_(None), now), else_=UsageInvoice.failed_at),
                    )
                )
                session.commit()
                try:
                    from cyberplat.billing.metrics import inc_job_failure_reason

                    inc_job_failure_reason(str(provider_name or "unknown"), str(err_code or "unknown"), 1)
                except Exception:
                    pass
                try:
                    from cyberplat.billing.metrics import observe_job_duration

                    observe_job_duration(str(getattr(db_job, "locked_by", "") or "unknown"), str(getattr(db_job, "provider", "") or ""), time.monotonic() - t0)
                except Exception:
                    pass
                return "failed"

            next_at = compute_next_attempt_at(current_attempt).isoformat()
            session.execute(
                update(BillingJob)
                .where(BillingJob.id == db_job.id)
                .values(
                    status="pending_retry",
                    attempt_count=current_attempt,
                    finished_at=now,
                    updated_at=now,
                    locked_at=None,
                    locked_by=None,
                    next_attempt_at=next_at,
                    last_error_code=err_code,
                    last_error_message=err_msg,
                )
            )
            # Keep invoice in processing for a retryable failure.
            session.execute(
                update(UsageInvoice)
                .where(UsageInvoice.id == inv.id)
                .where(UsageInvoice.tenant_id == inv.tenant_id)
                .where(UsageInvoice.payment_status != "processing")
                .values(payment_status="processing", payment_status_updated_at=now)
            )
            session.commit()
            try:
                from cyberplat.billing.metrics import observe_job_duration

                observe_job_duration(str(getattr(db_job, "locked_by", "") or "unknown"), str(getattr(db_job, "provider", "") or ""), time.monotonic() - t0)
            except Exception:
                pass
            return "retried"

        # Payment created successfully -> job succeeded; final paid/failed is reconciled by webhooks.
        session.execute(
            update(BillingJob)
            .where(BillingJob.id == job.id)
            .where(BillingJob.status == "processing")
            .values(
                status="succeeded",
                finished_at=now,
                updated_at=now,
                locked_at=None,
                locked_by=None,
                provider_ref=result.provider_ref,
                last_error_code=None,
                last_error_message=None,
                next_attempt_at=None,
            )
        )
        payment_status = "paid" if result.status == "paid" else ("failed" if result.status == "failed" else "processing")
        paid_was_none = getattr(inv, "paid_at", None) in (None, "")
        session.execute(
            update(UsageInvoice)
            .where(UsageInvoice.id == job.invoice_id)
            .where(UsageInvoice.tenant_id == job.tenant_id)
            .where(UsageInvoice.payment_status != payment_status)
            .values(
                **(
                    {
                        "payment_status": payment_status,
                        "payment_status_updated_at": now,
                    }
                    | (
                        {"paid_at": case((UsageInvoice.paid_at.is_(None), now), else_=UsageInvoice.paid_at)}
                        if payment_status == "paid"
                        else {}
                    )
                    | (
                        {"failed_at": case((UsageInvoice.failed_at.is_(None), now), else_=UsageInvoice.failed_at)}
                        if payment_status == "failed"
                        else {}
                    )
                )
            )
        )
        session.commit()
        if payment_status == "paid" and paid_was_none:
            try:
                finalized_at = getattr(inv, "finalized_at", None)
                if finalized_at:
                    dt_final = datetime.fromisoformat(str(finalized_at))
                    if dt_final.tzinfo is None:
                        dt_final = dt_final.replace(tzinfo=timezone.utc)
                    dt_now = datetime.fromisoformat(str(now))
                    if dt_now.tzinfo is None:
                        dt_now = dt_now.replace(tzinfo=timezone.utc)
                    from cyberplat.billing.metrics import observe_invoice_time_to_paid

                    observe_invoice_time_to_paid(max(0.0, (dt_now - dt_final).total_seconds()))
            except Exception:
                pass
        try:
            from cyberplat.billing.metrics import observe_job_duration

            observe_job_duration(str(getattr(db_job, "locked_by", "") or "unknown"), str(getattr(db_job, "provider", "") or ""), time.monotonic() - t0)
        except Exception:
            pass
        return "succeeded"


def process_due_billing_jobs(limit: int = 50) -> int:
    stats = process_due_billing_jobs_stats(limit=limit)
    return int(stats.get("processed_count", 0) or 0)


def process_due_billing_jobs_stats(
    *, limit: int = 50, worker_id: Optional[str] = None
) -> Dict[str, int]:
    """
    Process due billing jobs and return iteration stats.
    Intended for the billing worker (logging/ops).
    """
    now = now_iso()
    due = claim_due_jobs(now, limit=limit, worker_id=worker_id)
    succeeded = 0
    failed = 0
    retried = 0
    skipped = 0
    for job in due:
        outcome = process_job(job, now)
        if outcome == "succeeded":
            succeeded += 1
        elif outcome == "failed":
            failed += 1
        elif outcome == "retried":
            retried += 1
        else:
            skipped += 1

    return {
        "processed_count": len(due),
        "succeeded_count": succeeded,
        "failed_count": failed,
        "retried_count": retried,
        "skipped_count": skipped,
    }

