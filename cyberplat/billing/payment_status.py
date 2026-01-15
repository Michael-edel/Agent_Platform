"""Unified payment_status updates for UsageInvoice (SLA v2.1).

Centralizes:
- timestamps (payment_status_updated_at, paid_at, failed_at)
- transition counters (from->to with provider)
- time-to-paid histogram (by provider, observed once)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from cyberplat.billing.metrics import (
    inc_invoice_payment_status_transition,
    observe_invoice_time_to_paid,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_invoice_payment_status(invoice: Any, new_status: str, *, provider: Optional[str] = None, now: Optional[str] = None) -> bool:
    """
    Update invoice.payment_status if changed. Returns True if changed, False if no-op.

    Idempotency:
    - paid_at/failed_at are set only on first transition to paid/failed.
    - repeated set to same status does nothing.
    """
    if invoice is None:
        return False

    now_iso = (now or "").strip() or _now_iso()
    new_s = str(new_status or "").strip().lower()
    old_s = str(getattr(invoice, "payment_status", "") or "").strip().lower()

    if not new_s or old_s == new_s:
        return False

    paid_was_none = getattr(invoice, "paid_at", None) in (None, "")

    setattr(invoice, "payment_status", new_s)
    setattr(invoice, "payment_status_updated_at", now_iso)

    if new_s == "paid" and getattr(invoice, "paid_at", None) in (None, ""):
        setattr(invoice, "paid_at", now_iso)

    if new_s == "failed" and getattr(invoice, "failed_at", None) in (None, ""):
        setattr(invoice, "failed_at", now_iso)

    inc_invoice_payment_status_transition(old_s or "unknown", new_s, provider)

    # Observe time-to-paid once (when paid_at first becomes set)
    if new_s == "paid" and paid_was_none:
        try:
            finalized_at = getattr(invoice, "finalized_at", None)
            paid_at = getattr(invoice, "paid_at", None)
            if finalized_at and paid_at:
                dt_final = datetime.fromisoformat(str(finalized_at))
                dt_paid = datetime.fromisoformat(str(paid_at))
                if dt_final.tzinfo is None:
                    dt_final = dt_final.replace(tzinfo=timezone.utc)
                if dt_paid.tzinfo is None:
                    dt_paid = dt_paid.replace(tzinfo=timezone.utc)
                p = (provider or "").strip().lower()
                if p not in {"stripe", "kaspi"}:
                    p = "unknown"
                observe_invoice_time_to_paid(p, max(0.0, (dt_paid - dt_final).total_seconds()))
        except Exception:
            # best-effort
            pass

    return True

