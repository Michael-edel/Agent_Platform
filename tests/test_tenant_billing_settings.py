import uuid
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_tenant_settings_override_default_provider(monkeypatch, tmp_path):
    db_path = tmp_path / "tenant_settings.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    monkeypatch.setenv("BILLING_DRY_RUN", "false")
    monkeypatch.setenv("BILLING_DEFAULT_PROVIDER", "kaspi")

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, BillingJob, Tenant, TenantBillingSettings, UsageInvoice
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-1"
    invoice_id = str(uuid.uuid4())
    now = _now_iso()

    with Session(engine) as session:
        session.add(Tenant(id=tenant_id, name="Tenant One", is_active=True, created_at=now, updated_at=now))
        session.add(
            TenantBillingSettings(
                tenant_id=tenant_id,
                default_provider="stripe",
                stripe_enabled=True,
                kaspi_enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            UsageInvoice(
                id=invoice_id,
                tenant_id=tenant_id,
                period_year=2026,
                period_month=1,
                currency="KZT",
                amount_cents=10,
                status="finalized",
                payment_status="unpaid",
                created_at=now,
                finalized_at=now,
                event_emitted_at=None,
            )
        )
        session.commit()

    from cyberplat.billing.domain.events import UsageInvoiceReady
    from app.billing.handlers import handle_usage_invoice_ready

    handle_usage_invoice_ready(UsageInvoiceReady(tenant_id=tenant_id, period="2026-01", invoice_id=invoice_id, amount_cents=10))

    with Session(engine) as session:
        job = session.execute(select(BillingJob).where(BillingJob.invoice_id == invoice_id)).scalar_one()
        assert job.provider == "stripe"


def test_provider_disabled_blocks_job_and_fails_invoice(monkeypatch, tmp_path):
    db_path = tmp_path / "tenant_settings_disabled.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    monkeypatch.setenv("BILLING_DRY_RUN", "false")
    monkeypatch.setenv("BILLING_DEFAULT_PROVIDER", "stripe")

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, BillingJob, Tenant, TenantBillingSettings, UsageInvoice
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-2"
    invoice_id = str(uuid.uuid4())
    now = _now_iso()

    with Session(engine) as session:
        session.add(Tenant(id=tenant_id, name="Tenant Two", is_active=True, created_at=now, updated_at=now))
        # Disable stripe for this tenant
        session.add(
            TenantBillingSettings(
                tenant_id=tenant_id,
                default_provider="stripe",
                stripe_enabled=False,
                kaspi_enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            UsageInvoice(
                id=invoice_id,
                tenant_id=tenant_id,
                period_year=2026,
                period_month=1,
                currency="KZT",
                amount_cents=10,
                status="finalized",
                payment_status="unpaid",
                created_at=now,
                finalized_at=now,
                event_emitted_at=None,
            )
        )
        session.commit()

    from cyberplat.billing.domain.events import UsageInvoiceReady
    from app.billing.handlers import handle_usage_invoice_ready

    handle_usage_invoice_ready(UsageInvoiceReady(tenant_id=tenant_id, period="2026-01", invoice_id=invoice_id, amount_cents=10))

    with Session(engine) as session:
        jobs = session.execute(select(BillingJob).where(BillingJob.invoice_id == invoice_id)).scalars().all()
        assert jobs == []
        inv = session.execute(select(UsageInvoice).where(UsageInvoice.id == invoice_id)).scalar_one()
        assert inv.payment_status == "failed"


def test_tenant_admin_view_writes_audit_log(monkeypatch, tmp_path):
    db_path = tmp_path / "tenant_audit.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from starlette.requests import Request

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, AdminAuditLog, Tenant
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant = Tenant(id="tenant-3", name="Tenant Three", is_active=True, created_at=_now_iso(), updated_at=_now_iso())

    scope = {"type": "http", "method": "POST", "path": "/admin", "headers": [], "session": {"admin_username": "admin", "admin_role": "platform_admin"}}
    request = Request(scope)

    from app.admin.views import TenantAdmin

    view = TenantAdmin()
    # simulate update hook
    import anyio

    async def _run():
        await view.after_model_change({"name": "Tenant Three"}, tenant, False, request)

    anyio.run(_run)

    with Session(engine) as session:
        row = session.execute(select(AdminAuditLog).where(AdminAuditLog.entity_type == "Tenant")).scalar_one_or_none()
        assert row is not None
        assert row.action in {"tenant_update", "tenant_deactivate"}

