"""Billing jobs processor (test-friendly, no sleep/cron).

- In dry-run: marks invoice paid immediately.
- In non-dry-run: creates provider payment and stores provider_ref; final paid/failed comes from webhooks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import BillingJob, UsageInvoice


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _is_dry_run() -> bool:
    return os.getenv("BILLING_DRY_RUN", "true").strip().lower() in {"1", "true", "yes"}


def compute_backoff_seconds(attempt_count: int) -> int:
    # attempt_count is already incremented for this attempt (1..)
    base = 10
    exp = max(attempt_count - 1, 0)
    return min(3600, base * (2**exp))


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


def fetch_due_jobs(now: str, limit: int = 50) -> List[DueJob]:
    engine = get_engine()
    with Session(engine) as session:
        rows = session.execute(
            select(
                BillingJob.id,
                BillingJob.tenant_id,
                BillingJob.invoice_id,
                BillingJob.provider,
                BillingJob.status,
                BillingJob.attempt_count,
                BillingJob.max_attempts,
                BillingJob.next_attempt_at,
            )
            .where(BillingJob.status.in_(["pending", "failed"]))
            .where((BillingJob.next_attempt_at.is_(None)) | (BillingJob.next_attempt_at <= now))
            .where(BillingJob.attempt_count < BillingJob.max_attempts)
            .order_by(
                BillingJob.next_attempt_at.is_not(None),  # nulls first
                BillingJob.created_at,
            )
            .limit(limit)
        ).all()
        return [
            DueJob(
                id=r[0],
                tenant_id=r[1],
                invoice_id=r[2],
                provider=str(r[3] or ""),
                status=r[4],
                attempt_count=int(r[5] or 0),
                max_attempts=int(r[6] or 0),
                next_attempt_at=r[7],
            )
            for r in rows
        ]


def claim_job(job_id: str, now: str) -> bool:
    engine = get_engine()
    with Session(engine) as session:
        res = session.execute(
            update(BillingJob)
            .where(BillingJob.id == job_id)
            .where(BillingJob.status.in_(["pending", "failed"]))
            .values(status="processing", processing_started_at=now, updated_at=now)
        )
        session.commit()
        return bool(getattr(res, "rowcount", 0) and getattr(res, "rowcount", 0) > 0)


def process_job(job: DueJob, now: str) -> None:
    engine = get_engine()
    with Session(engine) as session:
        # Increment attempt count for this attempt
        res = session.execute(
            update(BillingJob)
            .where(BillingJob.id == job.id)
            .where(BillingJob.status == "processing")
            .values(attempt_count=BillingJob.attempt_count + 1, updated_at=now)
        )
        session.commit()

        # Re-read attempt_count after increment to compute backoff
        current_attempt = session.execute(
            select(BillingJob.attempt_count).where(BillingJob.id == job.id)
        ).scalar_one_or_none()
        attempt_count = int(current_attempt or 0)

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
                    last_error_code=None,
                    last_error_message=None,
                    next_attempt_at=None,
                )
            )
            session.execute(
                update(UsageInvoice)
                .where(UsageInvoice.id == job.invoice_id)
                .where(UsageInvoice.tenant_id == job.tenant_id)
                .values(payment_status="paid")
            )
            session.commit()
            return

        inv = session.execute(
            select(UsageInvoice).where(UsageInvoice.id == job.invoice_id, UsageInvoice.tenant_id == job.tenant_id)
        ).scalar_one_or_none()
        if not inv:
            # Nothing to do - mark failed with backoff.
            backoff = compute_backoff_seconds(attempt_count)
            next_at = (datetime.fromisoformat(now) + timedelta(seconds=backoff)).isoformat()
            session.execute(
                update(BillingJob)
                .where(BillingJob.id == job.id)
                .where(BillingJob.status == "processing")
                .values(
                    status="failed",
                    finished_at=now,
                    updated_at=now,
                    last_error_code="invoice_not_found",
                    last_error_message="usage invoice not found",
                    next_attempt_at=next_at,
                )
            )
            session.commit()
            return

        provider_name = (job.provider or "").strip().lower()
        # Import inside to keep tests monkeypatch-friendly
        from cyberplat.billing.providers.registry import get_provider

        provider = get_provider(provider_name)
        if not provider:
            backoff = compute_backoff_seconds(attempt_count)
            next_at = (datetime.fromisoformat(now) + timedelta(seconds=backoff)).isoformat()
            session.execute(
                update(BillingJob)
                .where(BillingJob.id == job.id)
                .where(BillingJob.status == "processing")
                .values(
                    status="failed",
                    finished_at=now,
                    updated_at=now,
                    last_error_code="unknown_provider",
                    last_error_message=f"unknown provider: {provider_name}",
                    next_attempt_at=next_at,
                )
            )
            session.execute(
                update(UsageInvoice)
                .where(UsageInvoice.id == job.invoice_id)
                .where(UsageInvoice.tenant_id == job.tenant_id)
                .values(payment_status="failed")
            )
            session.commit()
            return

        try:
            result = provider.create_payment(inv)
        except NotImplementedError as e:
            backoff = compute_backoff_seconds(attempt_count)
            next_at = (datetime.fromisoformat(now) + timedelta(seconds=backoff)).isoformat()
            session.execute(
                update(BillingJob)
                .where(BillingJob.id == job.id)
                .where(BillingJob.status == "processing")
                .values(
                    status="failed",
                    finished_at=now,
                    updated_at=now,
                    last_error_code="not_implemented",
                    last_error_message=str(e),
                    next_attempt_at=next_at,
                )
            )
            session.execute(
                update(UsageInvoice)
                .where(UsageInvoice.id == job.invoice_id)
                .where(UsageInvoice.tenant_id == job.tenant_id)
                .values(payment_status="failed")
            )
            session.commit()
            return
        except Exception as e:
            backoff = compute_backoff_seconds(attempt_count)
            next_at = (datetime.fromisoformat(now) + timedelta(seconds=backoff)).isoformat()
            session.execute(
                update(BillingJob)
                .where(BillingJob.id == job.id)
                .where(BillingJob.status == "processing")
                .values(
                    status="failed",
                    finished_at=now,
                    updated_at=now,
                    last_error_code="provider_error",
                    last_error_message=str(e)[:200],
                    next_attempt_at=next_at,
                )
            )
            session.execute(
                update(UsageInvoice)
                .where(UsageInvoice.id == job.invoice_id)
                .where(UsageInvoice.tenant_id == job.tenant_id)
                .values(payment_status="failed")
            )
            session.commit()
            return

        # Payment created successfully -> job succeeded; final paid/failed is reconciled by webhooks.
        session.execute(
            update(BillingJob)
            .where(BillingJob.id == job.id)
            .where(BillingJob.status == "processing")
            .values(
                status="succeeded",
                finished_at=now,
                updated_at=now,
                provider_ref=result.provider_ref,
                last_error_code=None,
                last_error_message=None,
                next_attempt_at=None,
            )
        )
        if result.status == "paid":
            payment_status = "paid"
        elif result.status == "failed":
            payment_status = "failed"
        else:
            payment_status = "processing"
        session.execute(
            update(UsageInvoice)
            .where(UsageInvoice.id == job.invoice_id)
            .where(UsageInvoice.tenant_id == job.tenant_id)
            .values(payment_status=payment_status)
        )
        session.commit()


def process_due_billing_jobs(limit: int = 50) -> int:
    now = now_iso()
    due = fetch_due_jobs(now, limit=limit)
    processed = 0
    for job in due:
        if claim_job(job.id, now):
            process_job(job, now)
            processed += 1
    return processed

