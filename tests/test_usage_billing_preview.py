"""Tests for usage-based billing v1 preview endpoint."""

import uuid
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "false")


def test_usage_billing_preview_counts_completed_only_and_is_idempotent(tmp_path, monkeypatch):
    # Isolated DB.
    db_path = tmp_path / "usage_preview.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import (
        Base,
        AgentSKU,
        TenantAgent,
        TenantAgentSubscription,
        TenantPortalToken,
        TenantUsageMonthly,
        AgentExecution,
    )
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-usage"
    now = datetime.now(timezone.utc).isoformat()
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    year = int(period.split("-")[0])
    month = int(period.split("-")[1])

    sku_id = str(uuid.uuid4())
    agent_code = "demo.sentiment_basic"

    # Seed portal token for auth
    from app.api.tenant_portal import hash_token

    portal_key = "test-portal-key"

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

    # API app with both routers.
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from app.api.agents import router as agents_router
    from app.api.tenant_portal import router as tenant_router

    app = FastAPI()
    app.include_router(agents_router)  # already has /api/v1/agents prefix
    app.include_router(tenant_router, prefix="/api/v1")
    client = TestClient(app)

    # Create N accepted executions.
    N = 3
    exec_ids: list[str] = []
    for i in range(N):
        r = client.post(
            f"/api/v1/agents/{agent_code}/execute",
            headers={"X-Tenant-ID": tenant_id},
            json={"input": {"text": "good excellent"}, "idempotency_key": f"u-{i}"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"
        exec_ids.append(r.json()["execution_id"])

    # Process all pending executions to completed.
    from app.agents.executor import claim_next_execution, run_execution

    for _ in range(N):
        with Session(engine) as session:
            claimed = claim_next_execution(session)
            assert claimed is not None
            run_execution(session, claimed)

    # Verify aggregation row and counted_at set.
    with Session(engine) as session:
        row = session.execute(
            select(TenantUsageMonthly).where(
                TenantUsageMonthly.tenant_id == tenant_id,
                TenantUsageMonthly.agent_code == agent_code,
                TenantUsageMonthly.year == year,
                TenantUsageMonthly.month == month,
            )
        ).scalar_one()
        assert row.completed_executions == N

        ex = session.execute(select(AgentExecution).where(AgentExecution.id == exec_ids[0])).scalar_one()
        assert ex.usage_counted_at is not None

    # Idempotency: calling run_execution again on completed must not double count.
    with Session(engine) as session:
        ex = session.execute(select(AgentExecution).where(AgentExecution.id == exec_ids[0])).scalar_one()
        run_execution(session, ex)

    with Session(engine) as session:
        row2 = session.execute(
            select(TenantUsageMonthly.completed_executions).where(
                TenantUsageMonthly.tenant_id == tenant_id,
                TenantUsageMonthly.agent_code == agent_code,
                TenantUsageMonthly.year == year,
                TenantUsageMonthly.month == month,
            )
        ).scalar_one()
        assert int(row2) == N

    # Preview endpoint
    resp = client.get(
        f"/api/v1/tenant/billing/usage-preview?period={period}",
        headers={"X-Tenant-ID": tenant_id, "X-Tenant-Portal-Key": portal_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["period"] == period
    lines = {l["agent_code"]: l for l in data["lines"]}
    assert agent_code in lines

    line = lines[agent_code]
    assert line["used"] == N
    assert line["included"] == 1
    assert line["billable"] == max(0, N - 1)
    assert line["price_cents"] == 10
    assert line["amount_cents"] == line["billable"] * 10
    assert data["totals"]["amount_cents"] == line["amount_cents"]

