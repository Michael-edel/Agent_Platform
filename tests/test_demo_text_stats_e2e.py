"""E2E test for demo.text_stats agent via built-in executor."""

import uuid
import pytest
from datetime import datetime, timezone


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


def test_demo_text_stats_end_to_end(tmp_path, monkeypatch):
    # Use isolated SQLite file DB per test (Windows-safe).
    db_path = tmp_path / "demo_text_stats.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, AgentSKU, TenantAgent, AgentExecution
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-demo"
    now = datetime.now(timezone.utc).isoformat()
    sku_id = str(uuid.uuid4())

    # Seed SKU + enablement for tenant (free agent, no subscription required).
    with Session(engine) as session:
        session.add(
            AgentSKU(
                id=sku_id,
                code="demo.text_stats",
                name="Demo: Text Stats",
                description="Counts chars/words/lines and top words",
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

    # Mount real API router.
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from app.api.agents import router as agents_router

    app = FastAPI()
    app.include_router(agents_router)
    client = TestClient(app)

    payload = {"text": "Привет мир\nHello world"}
    resp = client.post(
        "/api/v1/agents/demo.text_stats/execute",
        headers={"X-Tenant-ID": tenant_id},
        json={"input": payload, "idempotency_key": "k-demo-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"
    execution_id = data["execution_id"]

    # accepted -> running (claim) -> completed (run)
    from app.agents.executor import claim_next_execution, run_execution

    with Session(engine) as session:
        claimed = claim_next_execution(session)
        assert claimed is not None
        assert claimed.id == execution_id
        assert claimed.status == "running"

    # Verify running visible via API.
    resp_running = client.get(
        f"/api/v1/agents/executions/{execution_id}",
        headers={"X-Tenant-ID": tenant_id},
    )
    assert resp_running.status_code == 200
    assert resp_running.json()["status"] == "running"

    with Session(engine) as session:
        execution = session.execute(
            select(AgentExecution).where(AgentExecution.id == execution_id)
        ).scalar_one()
        run_execution(session, execution)

    resp_done = client.get(
        f"/api/v1/agents/executions/{execution_id}",
        headers={"X-Tenant-ID": tenant_id},
    )
    assert resp_done.status_code == 200
    done = resp_done.json()
    assert done["status"] == "completed"
    assert done["result"] is not None

    result = done["result"]
    assert result["lines"] == 2
    assert result["words"] >= 2
    assert result["chars"] >= 1
    assert result["top_words"], "top_words must not be empty"
    assert result["language_guess"] == "mixed"
    assert "meta" in result
    assert result["meta"]["duration_ms"] >= 0

