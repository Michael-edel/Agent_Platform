"""Test cooperative cancellation for running execution."""

import importlib
import threading
import time
import uuid
from datetime import datetime, timezone


def test_running_cancel_cooperative(tmp_path, monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "true")
    # Avoid default timeout.
    monkeypatch.delenv("AGENT_DEFAULT_TIMEOUT_SECONDS", raising=False)

    # Reload agent metrics module to ensure clean lazy state for this test.
    import app.agents.metrics as agent_metrics
    importlib.reload(agent_metrics)

    db_path = tmp_path / "running_cancel.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base, AgentSKU, TenantAgent, AgentExecution
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    engine = get_engine()
    Base.metadata.create_all(engine)

    tenant_id = "tenant-run-cancel"
    now = datetime.now(timezone.utc).isoformat()
    sku_id = str(uuid.uuid4())
    agent_code = "demo.slow_coop"

    with Session(engine) as session:
        session.add(
            AgentSKU(
                id=sku_id,
                code=agent_code,
                name="Demo: Slow Coop",
                description="Slow cooperative runner",
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

    # Register slow cooperative runner.
    import cyberplat.agents.registry as reg

    class SlowCoopRunner:
        def run(self, payload: dict, *, tenant_id: str, execution_id, ctx=None):
            # Loop and cooperatively check cancel flag.
            for _ in range(50):
                if ctx is not None:
                    ctx.check_cancelled()
                time.sleep(0.01)
            return {"ok": True}

    monkeypatch.setitem(reg.AGENT_RUNNERS, agent_code, SlowCoopRunner())
    # Ensure no per-agent timeout.
    if agent_code in reg.AGENT_TIMEOUT_SECONDS:
        d = dict(reg.AGENT_TIMEOUT_SECONDS)
        d.pop(agent_code, None)
        monkeypatch.setattr(reg, "AGENT_TIMEOUT_SECONDS", d)

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

    # Create accepted execution.
    resp = client.post(
        f"/api/v1/agents/{agent_code}/execute",
        headers={"X-Tenant-ID": tenant_id},
        json={"input": {"text": "x"}, "idempotency_key": "rc-1"},
    )
    assert resp.status_code == 200
    execution_id = resp.json()["execution_id"]

    from app.agents.executor import claim_next_execution, run_execution

    # Claim to running and start execution in background thread.
    with Session(engine) as session:
        claimed = claim_next_execution(session)
        assert claimed is not None
        assert claimed.id == execution_id

    def _run():
        with Session(engine) as session:
            ex = session.execute(select(AgentExecution).where(AgentExecution.id == execution_id)).scalar_one()
            run_execution(session, ex)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    # Request cancellation while running.
    cancel_resp = client.post(
        f"/api/v1/executions/{execution_id}/cancel",
        headers={"X-Tenant-ID": tenant_id},
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "running"
    assert cancel_resp.json()["cancel_requested"] is True

    t.join(timeout=5.0)
    assert not t.is_alive()

    with Session(engine) as session:
        ex = session.execute(select(AgentExecution).where(AgentExecution.id == execution_id)).scalar_one()
        assert ex.status == "failed"
        assert ex.error_code == "cancelled"

    metrics_text = client.get("/metrics").text
    assert f'agent_execution_failed_total{{agent_code="{agent_code}",error_code="cancelled"}}' in metrics_text

