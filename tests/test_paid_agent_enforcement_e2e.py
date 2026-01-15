"""E2E test for paid agent 402 gating + happy path (demo.sentiment_basic)."""

import importlib
import uuid
from datetime import datetime, timezone

import pytest


def test_paid_agent_enforcement_and_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "true")

    # Reload agent metrics module to ensure clean lazy state for this test.
    import app.agents.metrics as agent_metrics

    importlib.reload(agent_metrics)

    db_path = tmp_path / "paid_agent.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import (
        Base,
        AgentSKU,
        TenantAgent,
        TenantAgentSubscription,
        AgentExecution,
    )
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-paid"
    now = datetime.now(timezone.utc).isoformat()
    sku_id = str(uuid.uuid4())

    with Session(engine) as session:
        session.add(
            AgentSKU(
                id=sku_id,
                code="demo.sentiment_basic",
                name="Demo: Sentiment Basic",
                description="Paid demo agent",
                status="active",
                pricing_model="subscription",
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
        session.commit()

    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from app.api.agents import router as agents_router
    from cyberplat.observability.metrics import metrics_endpoint

    app = FastAPI()
    app.include_router(agents_router)

    @app.get("/metrics")
    def metrics():
        return metrics_endpoint()

    client = TestClient(app)

    # No subscription -> 402
    resp_402 = client.post(
        "/api/v1/agents/demo.sentiment_basic/execute",
        headers={"X-Tenant-ID": tenant_id},
        json={"input": {"text": "good excellent"}, "idempotency_key": "paid-1"},
    )
    assert resp_402.status_code == 402
    data_402 = resp_402.json()
    assert data_402["error"] == "agent_addon_inactive"

    # Activate subscription
    with Session(engine) as session:
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
        session.commit()

    # With subscription -> accepted
    resp_ok = client.post(
        "/api/v1/agents/demo.sentiment_basic/execute",
        headers={"X-Tenant-ID": tenant_id},
        json={"input": {"text": "good excellent"}, "idempotency_key": "paid-2"},
    )
    assert resp_ok.status_code == 200
    accepted = resp_ok.json()
    assert accepted["status"] == "accepted"
    execution_id = accepted["execution_id"]

    # Run executor (claim -> run)
    from app.agents.executor import claim_next_execution, run_execution

    with Session(engine) as session:
        claimed = claim_next_execution(session)
        assert claimed is not None
        assert claimed.id == execution_id
        assert claimed.status == "running"

    with Session(engine) as session:
        execution = session.execute(
            select(AgentExecution).where(AgentExecution.id == execution_id)
        ).scalar_one()
        run_execution(session, execution)

    # Completed with label/score + meta.duration_ms
    resp_done = client.get(
        f"/api/v1/agents/executions/{execution_id}",
        headers={"X-Tenant-ID": tenant_id},
    )
    assert resp_done.status_code == 200
    done = resp_done.json()
    assert done["status"] == "completed"
    assert done["result"]["label"] in {"positive", "neutral", "negative"}
    assert isinstance(done["result"]["score"], float)
    assert done["result"]["meta"]["duration_ms"] >= 0

    # Metrics should include completed for this agent_code.
    metrics_text = client.get("/metrics").text
    assert 'agent_execution_completed_total{agent_code="demo.sentiment_basic"}' in metrics_text

