"""Billing jobs: provider create_payment + webhook reconciliation (mocked)."""

import uuid
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "false")


def _seed_tenant_with_usage_priced_agent(engine, tenant_id: str, portal_key: str) -> str:
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
                usage_included_per_month=0,
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

    return agent_code


def test_provider_create_payment_and_webhook_reconcile(tmp_path, monkeypatch):
    # Non-dry-run: create payment via provider, then reconcile by webhook handlers.
    db_path = tmp_path / "billing_jobs_provider.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    monkeypatch.setenv("BILLING_DRY_RUN", "false")
    monkeypatch.setenv("BILLING_DEFAULT_PROVIDER", "stripe")

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, BillingJob, UsageInvoice
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-billing-providers"
    portal_key = "portal-key-providers"
    agent_code = _seed_tenant_with_usage_priced_agent(engine, tenant_id, portal_key)

    # Build API app for execute + invoice finalize.
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from app.api.agents import router as agents_router
    from app.api.tenant_portal import router as tenant_router

    app = FastAPI()
    app.include_router(agents_router)
    app.include_router(tenant_router, prefix="/api/v1")
    client = TestClient(app)

    # Create 1 completed execution so invoice amount > 0.
    r = client.post(
        f"/api/v1/agents/{agent_code}/execute",
        headers={"X-Tenant-ID": tenant_id},
        json={"input": {"text": "good"}, "idempotency_key": "prov-1"},
    )
    assert r.status_code == 200

    from app.agents.executor import claim_next_execution, run_execution

    with Session(engine) as session:
        ex = claim_next_execution(session)
        assert ex is not None
        run_execution(session, ex)

    period = datetime.now(timezone.utc).strftime("%Y-%m")
    fin = client.post(
        f"/api/v1/tenant/billing/usage-invoices/{period}/finalize",
        headers={"X-Tenant-ID": tenant_id, "X-Tenant-Portal-Key": portal_key},
    )
    assert fin.status_code == 200
    invoice_id = fin.json()["invoice_id"]

    # Mock provider registry
    class FakeStripeProvider:
        name = "stripe"

        def create_payment(self, invoice):
            from cyberplat.billing.providers.base import ProviderCreateResult

            return ProviderCreateResult(provider_ref="pi_test_123", status="processing", raw={"id": "pi_test_123"})

    import cyberplat.billing.providers.registry as reg

    monkeypatch.setattr(reg, "get_provider", lambda name: FakeStripeProvider() if name == "stripe" else None)

    from cyberplat.billing.jobs import process_due_billing_jobs

    processed = process_due_billing_jobs(limit=10)
    assert processed >= 1

    with Session(engine) as session:
        job = session.execute(select(BillingJob).where(BillingJob.invoice_id == invoice_id)).scalar_one()
        assert job.status == "succeeded"
        assert job.provider == "stripe"
        assert job.provider_ref == "pi_test_123"

        inv = session.execute(select(UsageInvoice).where(UsageInvoice.id == invoice_id)).scalar_one()
        assert inv.payment_status == "processing"

    # Simulate webhook reconciliation handler (idempotent)
    from cyberplat.billing.infrastructure.stripe_webhook_handlers import handle_stripe_payment_intent_succeeded

    event = {"type": "payment_intent.succeeded", "data": {"object": {"id": "pi_test_123"}}}
    ok, tid, err = handle_stripe_payment_intent_succeeded(event, subscription_repo=None)  # type: ignore[arg-type]
    assert ok is True
    assert err is None

    with Session(engine) as session:
        inv = session.execute(select(UsageInvoice).where(UsageInvoice.id == invoice_id)).scalar_one()
        assert inv.payment_status == "paid"

    # Idempotent second call
    ok2, _, _ = handle_stripe_payment_intent_succeeded(event, subscription_repo=None)  # type: ignore[arg-type]
    assert ok2 is True

