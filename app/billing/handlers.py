"""Billing event handlers (dry-run safe)."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from cyberplat.billing.domain.events import UsageInvoiceReady
from cyberplat.product.infrastructure.database import get_engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _is_dry_run() -> bool:
    return os.getenv("BILLING_DRY_RUN", "true").strip().lower() in {"1", "true", "yes"}

def _default_provider() -> str:
    raw = os.getenv("BILLING_DEFAULT_PROVIDER", "kaspi").strip().lower()
    return raw if raw in {"kaspi", "stripe"} else "kaspi"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_billing_job_for_invoice(tenant_id: str, invoice_id: str, *, provider: str = "dry_run") -> None:
    """
    Idempotently create a billing job for a usage invoice.

    One invoice -> at most one job (unique idempotency_key).
    """
    engine = get_engine()
    now = _now_iso()
    job_id = str(uuid.uuid4())
    idem_key = f"usage_invoice:{invoice_id}"

    with engine.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO billing_jobs (
                    id, tenant_id, invoice_id, provider, status,
                    attempt_count, max_attempts,
                    created_at, updated_at, idempotency_key
                )
                VALUES (
                    :id, :tenant_id, :invoice_id, :provider, 'pending',
                    0, 10,
                    :now, :now, :idempotency_key
                )
                ON CONFLICT (idempotency_key) DO NOTHING
                """
            ),
            {
                "id": job_id,
                "tenant_id": tenant_id,
                "invoice_id": invoice_id,
                "provider": provider,
                "now": now,
                "idempotency_key": idem_key,
            },
        )

        # Mark invoice payment_status as processing when job exists.
        conn.execute(
            text(
                """
                UPDATE usage_invoices
                SET payment_status = CASE
                    WHEN payment_status = 'unpaid' THEN 'processing'
                    ELSE payment_status
                END
                WHERE id = :invoice_id AND tenant_id = :tenant_id
                """
            ),
            {"invoice_id": invoice_id, "tenant_id": tenant_id},
        )
        conn.commit()


def handle_usage_invoice_ready(event: UsageInvoiceReady) -> None:
    """
    Create billing job for finalized usage invoice (idempotent).

    In dry-run mode we only enqueue a job; processing is done by the job processor.
    """
    provider = "dry_run" if _is_dry_run() else _default_provider()
    ensure_billing_job_for_invoice(event.tenant_id, event.invoice_id, provider=provider)
    logger.info(
        "UsageInvoiceReady queued: tenant=%s period=%s invoice_id=%s amount_cents=%s dry_run=%s",
        event.tenant_id,
        event.period,
        event.invoice_id,
        event.amount_cents,
        _is_dry_run(),
    )

