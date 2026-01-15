"""Tests for usage invoice finalization (dry-run, idempotent)."""

import uuid
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "false")


def test_usage_invoice_finalize_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("BILLING_DRY_RUN", "true")

    db_path = tmp_path / "usage_invoice.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import (
        Base,
        AgentSKU,
        TenantAgent,
        TenantAgentSubscription,
        TenantPortalToken,
        UsageInvoice,
        UsageInvoiceLine,
    )
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-invoice"
    now = datetime.now(timezone.utc).isoformat()
    period = datetime.now(timezone.utc).strftime("%Y-%m")

    year = int(period.split("-")[0])
    month = int(period.split("-")[1])

    sku_id = str(uuid.uuid4())
    agent_code = "demo.sentiment_basic"

    # Seed portal token for auth
    from app.api.tenant_portal import hash_token

    portal_key = "invoice-portal-key"

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

    # Create 3 completed executions to generate usage (included=1, price=10 => amount=20)
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from app.api.agents import router as agents_router
    from app.api.tenant_portal import router as tenant_router

    app = FastAPI()
    app.include_router(agents_router)
    app.include_router(tenant_router, prefix="/api/v1")
    client = TestClient(app)

    exec_ids = []
    for i in range(3):
        r = client.post(
            f"/api/v1/agents/{agent_code}/execute",
            headers={"X-Tenant-ID": tenant_id},
            json={"input": {"text": "good"}, "idempotency_key": f"inv-{i}"},
        )
        assert r.status_code == 200
        exec_ids.append(r.json()["execution_id"])

    from app.agents.executor import claim_next_execution, run_execution

    for _ in range(3):
        with Session(engine) as session:
            ex = claim_next_execution(session)
            assert ex is not None
            run_execution(session, ex)

    # Finalize invoice (POST)
    resp = client.post(
        f"/api/v1/tenant/billing/usage-invoices/{period}/finalize",
        headers={"X-Tenant-ID": tenant_id, "X-Tenant-Portal-Key": portal_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    invoice_id = data["invoice_id"]
    assert data["status"] == "finalized"
    assert data["period"] == period
    assert data["totals"]["amount_cents"] == 20

    # Repeat finalize -> same invoice_id, no duplication
    resp2 = client.post(
        f"/api/v1/tenant/billing/usage-invoices/{period}/finalize",
        headers={"X-Tenant-ID": tenant_id, "X-Tenant-Portal-Key": portal_key},
    )
    assert resp2.status_code == 200
    assert resp2.json()["invoice_id"] == invoice_id
    assert resp2.json()["totals"]["amount_cents"] == 20

    # GET invoice
    get_resp = client.get(
        f"/api/v1/tenant/billing/usage-invoices/{period}",
        headers={"X-Tenant-ID": tenant_id, "X-Tenant-Portal-Key": portal_key},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["invoice_id"] == invoice_id

    # Check DB: single invoice + lines, and event_emitted_at set and stable
    with Session(engine) as session:
        inv = session.execute(select(UsageInvoice).where(UsageInvoice.id == invoice_id)).scalar_one()
        emitted_1 = inv.event_emitted_at
        assert emitted_1 is not None

        line_count = session.execute(
            select(UsageInvoiceLine).where(UsageInvoiceLine.invoice_id == invoice_id)
        ).scalars().all()
        assert len(line_count) >= 1

    # Finalize again and ensure event_emitted_at unchanged (idempotent)
    _ = client.post(
        f"/api/v1/tenant/billing/usage-invoices/{period}/finalize",
        headers={"X-Tenant-ID": tenant_id, "X-Tenant-Portal-Key": portal_key},
    )
    with Session(engine) as session:
        inv2 = session.execute(select(UsageInvoice).where(UsageInvoice.id == invoice_id)).scalar_one()
        assert inv2.event_emitted_at == emitted_1

