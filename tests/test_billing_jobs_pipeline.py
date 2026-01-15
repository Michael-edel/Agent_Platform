"""Tests for billing jobs pipeline around UsageInvoiceReady (dry-run, retries)."""

import uuid
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "false")


def _seed_tenant_with_usage_priced_agent(engine, tenant_id: str, portal_key: str) -> tuple[str, str]:
    from cyberplat.product.infrastructure.models import (
        AgentSKU,
        TenantAgent,
        TenantAgentSubscription,
        TenantPortalToken,
    )
    from app.api.tenant_portal import hash_token
    from sqlalchemy.orm import Session

    now = datetime.now(timezone.utc).isoformat()
    sku_id = str(uuid.uuid4())
    agent_code = "demo.sentiment_basic"

    with Session(engine) as session:
        session.add(
            AgentSKU(
                id=sku_id,
                code=agent_code,
                name="Demo: Sentiment Basic",
                description="Paid demo agent",
                status="active",
                pricing_model="subscription",
                usage_enabled=True,
                usage_unit="execution",
                usage_price_cents=10,
                usage_included_per_month=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TenantAgent(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                agent_sku_id=sku_id,
                status="enabled",
                activated_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TenantAgentSubscription(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                agent_sku_id=sku_id,
                status="active",
                starts_at=now,
                ends_at=None,
                cancel_at_period_end=0,
                source="admin",
                external_ref=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TenantPortalToken(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                token_hash=hash_token(portal_key),
                token_prefix=portal_key[:8],
                created_at=now,
                revoked_at=None,
            )
        )
        session.commit()

    return agent_code, sku_id


def test_billing_jobs_pipeline_dry_run_and_backoff(tmp_path, monkeypatch):
    db_path = tmp_path / "billing_jobs.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, BillingJob, UsageInvoice
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-billing-jobs"
    portal_key = "billing-jobs-key"
    agent_code, _ = _seed_tenant_with_usage_priced_agent(engine, tenant_id, portal_key)

    # Build API app.
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from app.api.agents import router as agents_router
    from app.api.tenant_portal import router as tenant_router

    app = FastAPI()
    app.include_router(agents_router)
    app.include_router(tenant_router, prefix="/api/v1")
    client = TestClient(app)

    period = datetime.now(timezone.utc).strftime("%Y-%m")

    # Create 2 completed executions (included=1 => billable=1 => amount=10)
    exec_ids = []
    for i in range(2):
        r = client.post(
            f"/api/v1/agents/{agent_code}/execute",
            headers={"X-Tenant-ID": tenant_id},
            json={"input": {"text": "good"}, "idempotency_key": f"bj-{i}"},
        )
        assert r.status_code == 200
        exec_ids.append(r.json()["execution_id"])

    from app.agents.executor import claim_next_execution, run_execution

    for _ in range(2):
        with Session(engine) as session:
            ex = claim_next_execution(session)
            assert ex is not None
            run_execution(session, ex)

    # Finalize invoice -> emits event -> creates job and sets payment_status=processing
    monkeypatch.setenv("BILLING_DRY_RUN", "true")
    fin = client.post(
        f"/api/v1/tenant/billing/usage-invoices/{period}/finalize",
        headers={"X-Tenant-ID": tenant_id, "X-Tenant-Portal-Key": portal_key},
    )
    assert fin.status_code == 200
    invoice_id = fin.json()["invoice_id"]

    with Session(engine) as session:
        inv = session.execute(select(UsageInvoice).where(UsageInvoice.id == invoice_id)).scalar_one()
        assert inv.payment_status in {"processing", "unpaid"}
        jobs = session.execute(select(BillingJob).where(BillingJob.invoice_id == invoice_id)).scalars().all()
        assert len(jobs) == 1

    # Process due jobs -> succeeded and invoice paid
    from cyberplat.billing.jobs import process_due_billing_jobs

    processed = process_due_billing_jobs(limit=10)
    assert processed >= 1

    with Session(engine) as session:
        job = session.execute(select(BillingJob).where(BillingJob.invoice_id == invoice_id)).scalar_one()
        assert job.status == "succeeded"
        inv = session.execute(select(UsageInvoice).where(UsageInvoice.id == invoice_id)).scalar_one()
        assert inv.payment_status == "paid"

    # Idempotent event: calling handler again should not create duplicate jobs
    from cyberplat.billing.domain.events import UsageInvoiceReady
    from app.billing.handlers import handle_usage_invoice_ready

    handle_usage_invoice_ready(
        UsageInvoiceReady(tenant_id=tenant_id, period=period, invoice_id=invoice_id, amount_cents=10)
    )
    with Session(engine) as session:
        jobs = session.execute(select(BillingJob).where(BillingJob.invoice_id == invoice_id)).scalars().all()
        assert len(jobs) == 1

    # Backoff behavior when dry-run off
    monkeypatch.setenv("BILLING_DRY_RUN", "false")
    # Create a new invoice and job quickly by reusing handler with new invoice row.
    # We'll simulate by creating a job directly for existing invoice with a new idempotency_key by using a new invoice.
    # Create second invoice for a different period -> use direct insert.
    new_invoice_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    y = int(period.split("-")[0])
    m = int(period.split("-")[1])
    if m == 12:
        y2, m2 = y + 1, 1
    else:
        y2, m2 = y, m + 1
    period2 = f"{y2:04d}-{m2:02d}"
    with Session(engine) as session:
        session.add(
            UsageInvoice(
                id=new_invoice_id,
                tenant_id=tenant_id,
                period_year=y2,
                period_month=m2,
                currency="KZT",
                amount_cents=0,
                status="finalized",
                payment_status="unpaid",
                created_at=now,
                finalized_at=now,
                event_emitted_at=None,
            )
        )
        session.commit()

    handle_usage_invoice_ready(
        UsageInvoiceReady(tenant_id=tenant_id, period=period2, invoice_id=new_invoice_id, amount_cents=0)
    )
    processed2 = process_due_billing_jobs(limit=10)
    assert processed2 >= 1

    with Session(engine) as session:
        job2 = session.execute(select(BillingJob).where(BillingJob.invoice_id == new_invoice_id)).scalar_one()
        # Non-dry-run without provider implementation should schedule retry (pending_retry)
        assert job2.status == "pending_retry"
        assert job2.next_attempt_at is not None

    # Immediate re-run should not pick it up due to next_attempt_at in future
    processed3 = process_due_billing_jobs(limit=10)
    assert processed3 == 0

