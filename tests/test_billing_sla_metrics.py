import uuid
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_billing_sla_metrics_exist_and_time_to_paid_observed(monkeypatch, tmp_path):
    db_path = tmp_path / "sla_metrics.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from prometheus_client import REGISTRY, generate_latest
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, UsageInvoice

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "t-sla"
    invoice_id = str(uuid.uuid4())
    finalized_at = _now_iso()

    from cyberplat.billing.payment_status import set_invoice_payment_status

    with Session(engine) as session:
        inv = UsageInvoice(
            id=invoice_id,
            tenant_id=tenant_id,
            period_year=2026,
            period_month=1,
            currency="KZT",
            amount_cents=10,
            status="finalized",
            payment_status="unpaid",
            created_at=_now_iso(),
            finalized_at=finalized_at,
            event_emitted_at=None,
            payment_status_updated_at=None,
            paid_at=None,
            failed_at=None,
        )
        session.add(inv)
        session.commit()

        assert set_invoice_payment_status(inv, "processing", provider="stripe") is True
        session.commit()
        assert set_invoice_payment_status(inv, "paid", provider="stripe") is True
        session.commit()
        # Idempotent
        assert set_invoice_payment_status(inv, "paid", provider="stripe") is False
        session.commit()

    with Session(engine) as session:
        inv = session.execute(select(UsageInvoice).where(UsageInvoice.id == invoice_id)).scalar_one()
        assert inv.payment_status == "paid"
        assert inv.payment_status_updated_at is not None
        assert inv.paid_at is not None

    # Ensure metric families exist in exposition output
    body = generate_latest(REGISTRY).decode("utf-8", errors="ignore")
    assert "usage_invoices_payment_status_total" in body
    assert "usage_invoices_payment_status_transitions_total" in body
    assert "usage_invoice_time_to_paid_seconds_bucket" in body
    assert "provider=\"stripe\"" in body

    # Ensure failure reasons counter exists (touch once to emit labeled series)
    from cyberplat.billing.metrics import inc_job_failure_reason

    inc_job_failure_reason("stripe", "provider_error", 1)
    body2 = generate_latest(REGISTRY).decode("utf-8", errors="ignore")
    assert "billing_jobs_failure_reasons_total" in body2

