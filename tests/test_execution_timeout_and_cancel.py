"""Tests for timeout + cancellation (best-effort)."""

import importlib
import json
import time
import uuid
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    # Keep other tests stable; enable explicitly where needed.
    monkeypatch.setenv("METRICS_ENABLED", "false")


def test_timeout_marks_failed_with_timeout_error(tmp_path, monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "true")

    # Reload agent metrics module to ensure clean lazy state for this test.
    import app.agents.metrics as agent_metrics
    importlib.reload(agent_metrics)

    db_path = tmp_path / "timeout.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, AgentSKU, TenantAgent, AgentExecution
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-timeout"
    now = datetime.now(timezone.utc).isoformat()
    sku_id = str(uuid.uuid4())

    with Session(engine) as session:
        session.add(
            AgentSKU(
                id=sku_id,
                code="demo.slow",
                name="Demo: Slow",
                description="sleepy runner to test timeout",
                status="active",
                pricing_model="free",
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

    # Patch registry to add slow runner + short timeout.
    import cyberplat.agents.registry as reg

    class SlowRunner:
        def run(self, payload: dict, *, tenant_id: str, execution_id):
            time.sleep(0.2)
            return {"ok": True}

    monkeypatch.setitem(reg.AGENT_RUNNERS, "demo.slow", SlowRunner())
    monkeypatch.setenv("AGENT_DEFAULT_TIMEOUT_SECONDS", "0.05")

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

    resp = client.post(
        "/api/v1/agents/demo.slow/execute",
        headers={"X-Tenant-ID": tenant_id},
        json={"input": {"text": "x"}, "idempotency_key": "t-1"},
    )
    assert resp.status_code == 200
    execution_id = resp.json()["execution_id"]

    from app.agents.executor import claim_next_execution, run_execution

    with Session(engine) as session:
        claimed = claim_next_execution(session)
        assert claimed is not None
        run_execution(session, claimed)

    with Session(engine) as session:
        ex = session.execute(select(AgentExecution).where(AgentExecution.id == execution_id)).scalar_one()
        assert ex.status == "failed"
        assert ex.error_code == "timeout"
        err = json.loads(ex.result_json)
        assert err["error_code"] == "timeout"
        assert err["details"]["timeout_seconds"] is not None
        assert err["meta"]["duration_ms"] >= 0


def test_cancel_accepted_is_idempotent_and_counts_as_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "true")

    import app.agents.metrics as agent_metrics
    importlib.reload(agent_metrics)

    db_path = tmp_path / "cancel.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, AgentSKU, TenantAgent
    from sqlalchemy.orm import Session

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-cancel"
    now = datetime.now(timezone.utc).isoformat()
    sku_id = str(uuid.uuid4())

    with Session(engine) as session:
        session.add(
            AgentSKU(
                id=sku_id,
                code="demo.text_stats",
                name="Demo: Text Stats",
                description="free agent",
                status="active",
                pricing_model="free",
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
    from app.api.executions import router as executions_router
    from cyberplat.observability.metrics import metrics_endpoint

    app = FastAPI()
    app.include_router(agents_router)
    app.include_router(executions_router)

    @app.get("/metrics")
    def metrics():
        return metrics_endpoint()

    client = TestClient(app)

    # Create accepted execution (do not run executor).
    resp = client.post(
        "/api/v1/agents/demo.text_stats/execute",
        headers={"X-Tenant-ID": tenant_id},
        json={"input": {"text": "hi"}, "idempotency_key": "c-1"},
    )
    assert resp.status_code == 200
    execution_id = resp.json()["execution_id"]
    assert resp.json()["status"] == "accepted"

    # Cancel once -> rejected/cancelled
    r1 = client.post(
        f"/api/v1/executions/{execution_id}/cancel",
        headers={"X-Tenant-ID": tenant_id},
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "rejected"
    assert r1.json()["error_code"] == "cancelled"

    # Cancel again -> idempotent 200, same state
    r2 = client.post(
        f"/api/v1/executions/{execution_id}/cancel",
        headers={"X-Tenant-ID": tenant_id},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "rejected"
    assert r2.json()["error_code"] == "cancelled"

    metrics_text = client.get("/metrics").text
    assert 'agent_execution_failed_total{agent_code="demo.text_stats",error_code="cancelled"}' in metrics_text

