"""Billing event handlers (dry-run safe)."""

from __future__ import annotations

import logging
import os

from cyberplat.billing.domain.events import UsageInvoiceReady

logger = logging.getLogger(__name__)


def _is_dry_run() -> bool:
    return os.getenv("BILLING_DRY_RUN", "true").strip().lower() in {"1", "true", "yes"}


def handle_usage_invoice_ready(event: UsageInvoiceReady) -> None:
    """
    Dry-run integration for finalized usage invoices.

    For now we do not charge anything. In dry-run mode we only log.
    """
    if _is_dry_run():
        logger.info(
            "[DRY-RUN] UsageInvoiceReady: tenant=%s period=%s invoice_id=%s amount_cents=%s",
            event.tenant_id,
            event.period,
            event.invoice_id,
            event.amount_cents,
        )
        return

    # Safe no-op for now (future integration point).
    logger.info(
        "UsageInvoiceReady received (no-op): tenant=%s period=%s invoice_id=%s amount_cents=%s",
        event.tenant_id,
        event.period,
        event.invoice_id,
        event.amount_cents,
    )

