"""Tests for agent execution Prometheus metrics."""

import importlib
import uuid
from datetime import datetime, timezone

import pytest


def test_agent_metrics_exposed_via_metrics_endpoint(tmp_path, monkeypatch):
    # Ensure metrics modules see enabled env on import.
    monkeypatch.setenv("METRICS_ENABLED", "true")

    # Isolated DB per test.
    db_path = tmp_path / "metrics_exec.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Reload agent metrics module to ensure clean lazy state for this test.
    import app.agents.metrics as agent_metrics
    importlib.reload(agent_metrics)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, AgentSKU, TenantAgent
    from sqlalchemy.orm import Session

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-metrics"
    now = datetime.now(timezone.utc).isoformat()

    with Session(engine) as session:
        # Successful agent.
        sku_ok = AgentSKU(
            id=str(uuid.uuid4()),
            code="demo.text_stats",
            name="Demo: Text Stats",
            description="Counts chars/words/lines and top words",
            status="active",
            pricing_model="free",
            created_at=now,
            updated_at=now,
        )
        session.add(sku_ok)
        session.add(
            TenantAgent(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                agent_sku_id=sku_ok.id,
                status="enabled",
                activated_at=now,
                created_at=now,
                updated_at=now,
            )
        )

        # Runner-not-found agent (SKU exists, enabled, but no runner registered).
        sku_missing = AgentSKU(
            id=str(uuid.uuid4()),
            code="demo.missing",
            name="Demo: Missing Runner",
            description="Should fail with runner_not_found",
            status="active",
            pricing_model="free",
            created_at=now,
            updated_at=now,
        )
        session.add(sku_missing)
        session.add(
            TenantAgent(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                agent_sku_id=sku_missing.id,
                status="enabled",
                activated_at=now,
                created_at=now,
                updated_at=now,
            )
        )

        session.commit()

    # Minimal app: execution API + /metrics.
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

    # Create one successful execution.
    resp_ok = client.post(
        "/api/v1/agents/demo.text_stats/execute",
        headers={"X-Tenant-ID": tenant_id},
        json={"input": {"text": "Hi"}, "idempotency_key": "m-ok-1"},
    )
    assert resp_ok.status_code == 200

    # Create one runner_not_found execution.
    resp_fail = client.post(
        "/api/v1/agents/demo.missing/execute",
        headers={"X-Tenant-ID": tenant_id},
        json={"input": {"text": "Hi"}, "idempotency_key": "m-f-1"},
    )
    assert resp_fail.status_code == 200

    # Process both via built-in executor tick (claim -> run).
    from app.agents.executor import claim_next_execution, run_execution
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        ex1 = claim_next_execution(session)
        assert ex1 is not None
        run_execution(session, ex1)

    with OrmSession(engine) as session:
        ex2 = claim_next_execution(session)
        assert ex2 is not None
        run_execution(session, ex2)

    metrics_text = client.get("/metrics").text

    assert 'agent_execution_completed_total{agent_code="demo.text_stats"}' in metrics_text
    assert 'agent_execution_failed_total{agent_code="demo.missing",error_code="runner_not_found"}' in metrics_text

