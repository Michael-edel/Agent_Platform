import uuid
from datetime import datetime, timezone

import pytest


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_backoff_increases(monkeypatch):
    from cyberplat.billing.jobs import compute_next_attempt_at

    # Remove jitter for deterministic ordering
    import cyberplat.billing.jobs as jobs

    monkeypatch.setattr(jobs.random, "uniform", lambda a, b: 0.0)

    t1 = compute_next_attempt_at(1, base_seconds=10, max_seconds=3600)
    t2 = compute_next_attempt_at(2, base_seconds=10, max_seconds=3600)
    t3 = compute_next_attempt_at(3, base_seconds=10, max_seconds=3600)
    assert (t2 - t1).total_seconds() >= 9
    assert (t3 - t2).total_seconds() >= 19


def test_retry_transitions_pending_retry_then_failed(monkeypatch, tmp_path):
    # SQLite-only
    db_path = tmp_path / "retry_policy.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BILLING_DRY_RUN", "false")

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, UsageInvoice, BillingJob
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "t1"
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
                status="pending_retry",
                attempt_count=0,
                max_attempts=2,
                created_at=_now_iso(),
                updated_at=_now_iso(),
                processing_started_at=None,
                finished_at=None,
                idempotency_key=f"usage_invoice:{invoice_id}",
                provider_ref=None,
                last_error_code=None,
                last_error_message=None,
                next_attempt_at=_now_iso(),
                locked_at=None,
                locked_by=None,
                last_attempt_at=None,
            )
        )
        session.commit()

    # Mock provider to fail
    class FailingProvider:
        name = "stripe"

        def create_payment(self, invoice):
            raise RuntimeError("boom")

    import cyberplat.billing.providers.registry as reg

    monkeypatch.setattr(reg, "get_provider", lambda name: FailingProvider())

    import cyberplat.billing.jobs as jobs

    # Deterministic backoff
    monkeypatch.setattr(jobs.random, "uniform", lambda a, b: 0.0)

    # First attempt -> pending_retry with attempt_count=1
    processed = jobs.process_due_billing_jobs(limit=10)
    assert processed == 1
    with Session(engine) as session:
        job = session.execute(select(BillingJob).where(BillingJob.id == job_id)).scalar_one()
        assert job.status == "pending_retry"
        assert int(job.attempt_count) == 1
        assert job.next_attempt_at is not None

    # Force due immediately
    with Session(engine) as session:
        session.execute(
            select(BillingJob).where(BillingJob.id == job_id)
        )
        session.query(BillingJob).filter(BillingJob.id == job_id).update({"next_attempt_at": _now_iso()})
        session.commit()

    # Second attempt -> failed (max_attempts reached), next_attempt_at None
    processed2 = jobs.process_due_billing_jobs(limit=10)
    assert processed2 == 1
    with Session(engine) as session:
        job = session.execute(select(BillingJob).where(BillingJob.id == job_id)).scalar_one()
        assert job.status == "failed"
        assert int(job.attempt_count) == 2
        assert job.next_attempt_at is None


def test_locking_fallback_sqlite(monkeypatch, tmp_path):
    db_path = tmp_path / "locking.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BILLING_DRY_RUN", "true")

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, UsageInvoice, BillingJob
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "t1"
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
                provider="dry_run",
                status="pending",
                attempt_count=0,
                max_attempts=5,
                created_at=_now_iso(),
                updated_at=_now_iso(),
                processing_started_at=None,
                finished_at=None,
                idempotency_key=f"usage_invoice:{invoice_id}",
                provider_ref=None,
                last_error_code=None,
                last_error_message=None,
                next_attempt_at=None,
                locked_at=_now_iso(),  # locked already
                locked_by="worker-a",
                last_attempt_at=None,
            )
        )
        session.commit()

    import cyberplat.billing.jobs as jobs

    # Should not process because locked_at is set (best-effort)
    assert jobs.process_due_billing_jobs(limit=10) == 0
    with Session(engine) as session:
        job = session.execute(select(BillingJob).where(BillingJob.id == job_id)).scalar_one()
        assert job.status == "pending"


def test_retry_action_respects_max_attempts(monkeypatch, tmp_path):
    from starlette.testclient import TestClient
    import importlib

    db_path = tmp_path / "retry_action.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("METRICS_ENABLED", "false")

    monkeypatch.setenv("ADMIN_ENABLED", "true")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    monkeypatch.setenv("ADMIN_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_ROLE", "platform_admin")

    import app.main as main
    importlib.reload(main)
    client = TestClient(main.app)
    client.post("/admin/login", data={"username": "admin", "password": "testpass"}, follow_redirects=True)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, UsageInvoice, BillingJob
    from sqlalchemy.orm import Session

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "t1"
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
                status="failed",
                attempt_count=5,
                max_attempts=5,
                created_at=_now_iso(),
                updated_at=_now_iso(),
                processing_started_at=None,
                finished_at=None,
                idempotency_key=f"usage_invoice:{invoice_id}",
                provider_ref=None,
                last_error_code=None,
                last_error_message=None,
                next_attempt_at=None,
                locked_at=None,
                locked_by=None,
                last_attempt_at=None,
            )
        )
        session.commit()

    r = client.post(f"/admin/actions/billing-job/{job_id}/retry", follow_redirects=False)
    assert r.status_code in {302, 303}

    from sqlalchemy import select
    with Session(engine) as session:
        job = session.execute(select(BillingJob).where(BillingJob.id == job_id)).scalar_one()
        assert job.status == "failed"
