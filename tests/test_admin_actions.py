import importlib
import sys
import uuid
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_admin_app(monkeypatch, tmp_path, *, role: str, tenant_id: str | None = None):
    db_path = tmp_path / f"admin_actions_{role}.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("METRICS_ENABLED", "false")

    monkeypatch.setenv("ADMIN_ENABLED", "true")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    monkeypatch.setenv("ADMIN_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_ROLE", role)
    if tenant_id is not None:
        monkeypatch.setenv("ADMIN_TENANT_ID", tenant_id)
    else:
        monkeypatch.delenv("ADMIN_TENANT_ID", raising=False)

    import app.main as main

    importlib.reload(main)
    return main.app


def _login(client: TestClient):
    # Follow redirects to ensure session cookie is persisted for mounted /admin app.
    r = client.post("/admin/login", data={"username": "admin", "password": "testpass"}, follow_redirects=True)
    assert r.status_code != 404


def test_admin_actions_tenant_admin_forbidden(monkeypatch, tmp_path):
    app = _make_admin_app(monkeypatch, tmp_path, role="tenant_admin", tenant_id="tenant-1")
    client = TestClient(app)
    _login(client)

    r = client.post("/admin/actions/billing-job/some-job/retry", follow_redirects=False)
    assert r.status_code == 403


def test_admin_refresh_from_provider_stripe_updates_invoice_and_audits(monkeypatch, tmp_path):
    app = _make_admin_app(monkeypatch, tmp_path, role="platform_admin")
    client = TestClient(app)
    _login(client)

    # Prepare DB rows
    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, UsageInvoice, BillingJob, AdminAuditLog
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-1"
    invoice_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    with Session(engine) as session:
        session.add(
            UsageInvoice(
                id=invoice_id,
                tenant_id=tenant_id,
                period_year=2026,
                period_month=1,
                currency="KZT",
                amount_cents=10,
                status="finalized",
                payment_status="processing",
                created_at=_now_iso(),
                finalized_at=_now_iso(),
                event_emitted_at=None,
            )
        )
        session.add(
            BillingJob(
                id=job_id,
                tenant_id=tenant_id,
                invoice_id=invoice_id,
                provider="stripe",
                provider_ref="pi_test_123",
                status="succeeded",
                attempt_count=1,
                max_attempts=10,
                created_at=_now_iso(),
                updated_at=_now_iso(),
                processing_started_at=None,
                finished_at=_now_iso(),
                idempotency_key=f"usage_invoice:{invoice_id}",
                last_error_code=None,
                last_error_message=None,
                next_attempt_at=None,
            )
        )
        session.commit()

    # Mock Stripe SDK: allow retrieve only, forbid create.
    class FakeIntent:
        def __init__(self, status: str):
            self.status = status

    class FakePaymentIntent:
        @staticmethod
        def retrieve(_id: str):
            assert _id == "pi_test_123"
            return FakeIntent(status="succeeded")

        @staticmethod
        def create(*args, **kwargs):
            raise AssertionError("PaymentIntent.create must not be called from refresh action")

    class FakeStripe:
        api_key = None
        PaymentIntent = FakePaymentIntent

    sys.modules["stripe"] = FakeStripe
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test")

    r = client.post(f"/admin/actions/billing-job/{job_id}/refresh", follow_redirects=False)
    assert r.status_code in {302, 303}

    with Session(engine) as session:
        inv = session.execute(select(UsageInvoice).where(UsageInvoice.id == invoice_id)).scalar_one()
        assert inv.payment_status == "paid"
        audit = session.execute(
            select(AdminAuditLog).where(AdminAuditLog.action == "refresh_provider_status", AdminAuditLog.entity_id == job_id)
        ).scalar_one_or_none()
        assert audit is not None


def test_admin_mark_retry_sets_pending_retry_and_audits(monkeypatch, tmp_path):
    app = _make_admin_app(monkeypatch, tmp_path, role="platform_admin")
    client = TestClient(app)
    _login(client)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, UsageInvoice, BillingJob, AdminAuditLog
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-1"
    invoice_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    with Session(engine) as session:
        session.add(
            UsageInvoice(
                id=invoice_id,
                tenant_id=tenant_id,
                period_year=2026,
                period_month=1,
                currency="KZT",
                amount_cents=10,
                status="finalized",
                payment_status="failed",
                created_at=_now_iso(),
                finalized_at=_now_iso(),
                event_emitted_at=None,
            )
        )
        session.add(
            BillingJob(
                id=job_id,
                tenant_id=tenant_id,
                invoice_id=invoice_id,
                provider="stripe",
                provider_ref="pi_test_456",
                status="failed",
                attempt_count=2,
                max_attempts=10,
                created_at=_now_iso(),
                updated_at=_now_iso(),
                processing_started_at=None,
                finished_at=_now_iso(),
                idempotency_key=f"usage_invoice:{invoice_id}",
                last_error_code="provider_error",
                last_error_message="boom",
                next_attempt_at=None,
            )
        )
        session.commit()

    r = client.post(f"/admin/actions/billing-job/{job_id}/retry", follow_redirects=False)
    assert r.status_code in {302, 303}

    with Session(engine) as session:
        job = session.execute(select(BillingJob).where(BillingJob.id == job_id)).scalar_one()
        assert job.status == "pending_retry"
        assert job.next_attempt_at is not None
        audit = session.execute(
            select(AdminAuditLog).where(AdminAuditLog.action == "mark_retry", AdminAuditLog.entity_id == job_id)
        ).scalar_one_or_none()
        assert audit is not None

