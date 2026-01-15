"""Usage invoice finalization (usage pricing v1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import (
    AgentSKU,
    TenantAgent,
    TenantUsageMonthly,
    UsageInvoice,
    UsageInvoiceLine,
)
from cyberplat.billing.domain.events import UsageInvoiceReady
from app.billing.handlers import handle_usage_invoice_ready


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class FinalizedUsageInvoice:
    invoice: UsageInvoice
    lines: List[UsageInvoiceLine]


def finalize_usage_invoice(tenant_id: str, year: int, month: int, currency: str = "KZT") -> FinalizedUsageInvoice:
    """
    Finalize usage invoice for a tenant and billing period (idempotent).

    Creates snapshot rows in usage_invoices + usage_invoice_lines.
    Emits UsageInvoiceReady exactly once (event_emitted_at).
    """
    engine = get_engine()
    period = f"{year:04d}-{month:02d}"
    now = now_iso()

    with Session(engine, expire_on_commit=False) as session:
        existing = session.execute(
            select(UsageInvoice).where(
                UsageInvoice.tenant_id == tenant_id,
                UsageInvoice.period_year == year,
                UsageInvoice.period_month == month,
            )
        ).scalar_one_or_none()

        if existing:
            lines = session.execute(
                select(UsageInvoiceLine).where(UsageInvoiceLine.invoice_id == existing.id)
            ).scalars().all()
            return FinalizedUsageInvoice(invoice=existing, lines=list(lines))

        # Load enabled, usage-priced SKUs for tenant.
        skus = session.execute(
            select(AgentSKU)
            .join(TenantAgent, TenantAgent.agent_sku_id == AgentSKU.id)
            .where(TenantAgent.tenant_id == tenant_id)
            .where(TenantAgent.status == "enabled")
            .where(AgentSKU.status == "active")
            .where(AgentSKU.usage_enabled.is_(True))
            .order_by(AgentSKU.code)
        ).scalars().all()

        agent_codes = [s.code for s in skus]
        usage_map: dict[str, int] = {}
        if agent_codes:
            rows = session.execute(
                select(TenantUsageMonthly.agent_code, TenantUsageMonthly.completed_executions)
                .where(TenantUsageMonthly.tenant_id == tenant_id)
                .where(TenantUsageMonthly.year == year)
                .where(TenantUsageMonthly.month == month)
                .where(TenantUsageMonthly.agent_code.in_(agent_codes))
            ).all()
            for code, cnt in rows:
                usage_map[str(code)] = int(cnt or 0)

        invoice_id = str(uuid.uuid4())
        lines: List[UsageInvoiceLine] = []
        total_amount = 0

        for sku in skus:
            used = usage_map.get(sku.code, 0)
            included = int(sku.usage_included_per_month or 0)
            price_cents = int(sku.usage_price_cents or 0)
            unit = str(sku.usage_unit or "execution")
            billable = max(0, used - included)
            amount = billable * price_cents
            total_amount += amount

            lines.append(
                UsageInvoiceLine(
                    id=str(uuid.uuid4()),
                    invoice_id=invoice_id,
                    agent_code=sku.code,
                    unit=unit,
                    used=used,
                    included=included,
                    billable=billable,
                    price_cents=price_cents,
                    amount_cents=amount,
                )
            )

        invoice = UsageInvoice(
            id=invoice_id,
            tenant_id=tenant_id,
            period_year=year,
            period_month=month,
            currency=currency,
            amount_cents=total_amount,
            status="finalized",
            payment_status="unpaid",
            created_at=now,
            finalized_at=now,
            event_emitted_at=None,
        )

        session.add(invoice)
        for line in lines:
            session.add(line)
        session.commit()

        # Emit event once (best-effort) and mark emitted timestamp.
        event = UsageInvoiceReady(
            tenant_id=tenant_id,
            period=period,
            invoice_id=invoice_id,
            amount_cents=total_amount,
        )
        try:
            handle_usage_invoice_ready(event)
        finally:
            invoice.event_emitted_at = now
            session.add(invoice)
            session.commit()

        return FinalizedUsageInvoice(invoice=invoice, lines=lines)


def get_usage_invoice(tenant_id: str, year: int, month: int) -> Optional[FinalizedUsageInvoice]:
    engine = get_engine()
    with Session(engine, expire_on_commit=False) as session:
        inv = session.execute(
            select(UsageInvoice).where(
                UsageInvoice.tenant_id == tenant_id,
                UsageInvoice.period_year == year,
                UsageInvoice.period_month == month,
            )
        ).scalar_one_or_none()
        if not inv:
            return None
        lines = session.execute(
            select(UsageInvoiceLine).where(UsageInvoiceLine.invoice_id == inv.id)
        ).scalars().all()
        return FinalizedUsageInvoice(invoice=inv, lines=list(lines))

