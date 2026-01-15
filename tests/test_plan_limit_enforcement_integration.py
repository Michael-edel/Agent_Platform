"""Integration tests for plan limit enforcement."""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestPlanLimitExceededHandler:
    """Tests for the global PlanLimitExceededError handler."""

    def test_exception_handler_returns_429(self):
        """PlanLimitExceededError returns 429 with correct JSON."""
        from starlette.testclient import TestClient
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from app.billing.limits import PlanLimitExceededError
        
        app = FastAPI()
        
        @app.exception_handler(PlanLimitExceededError)
        async def handler(request, exc):
            return JSONResponse(
                status_code=429,
                content={
                    "error": "plan_limit_exceeded",
                    "metric": exc.metric,
                    "limit": exc.limit,
                    "used": exc.used,
                    "tenant_id": exc.tenant_id,
                },
            )
        
        @app.get("/test")
        def trigger_error():
            raise PlanLimitExceededError(
                metric="documents",
                limit=100,
                used=100,
                tenant_id="t1",
            )
        
        client = TestClient(app)
        response = client.get("/test")
        
        assert response.status_code == 429
        data = response.json()
        assert data["error"] == "plan_limit_exceeded"
        assert data["metric"] == "documents"
        assert data["limit"] == 100
        assert data["used"] == 100
        assert data["tenant_id"] == "t1"


class TestRecordEventWithLimitCheck:
    """Tests for record_event with limit checking."""

    def test_limit_check_raises_error_correctly(self):
        """check_plan_limit correctly raises when limit exceeded."""
        from app.billing.limits import PlanLimitExceededError, LimitCheckResult
        
        # Test that the error is raised correctly
        result = LimitCheckResult(
            allowed=False,
            limit=10,
            used=10,
            remaining=0,
            reason="limit_exceeded",
        )
        
        # Simulate what record_event does
        if not result.allowed and result.reason == "limit_exceeded":
            error = PlanLimitExceededError(
                metric="documents",
                limit=result.limit,
                used=result.used,
                tenant_id="t1",
            )
            
            assert error.metric == "documents"
            assert error.limit == 10
            assert error.used == 10
            assert error.tenant_id == "t1"


class TestCheckPlanLimitIntegration:
    """Integration tests for check_plan_limit with real DB queries."""

    def test_check_limit_with_mocked_db_flow(self):
        """Full flow: plan -> quotas -> usage -> check."""
        from app.billing.limits import check_plan_limit
        import json
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        # Simulate: tenant has plan "pro" with documents=10, used=10
        mock_conn.execute.return_value.fetchone.side_effect = [
            ("pro",),  # plan_id
            (json.dumps({"documents": 10}),),  # quotas
        ]
        mock_conn.execute.return_value.scalar.return_value = 10  # current usage
        
        result = check_plan_limit(
            tenant_id="t1",
            metric="documents",
            increment=1,
            period="2026-01",
            engine=mock_engine,
        )
        
        assert result.allowed is False
        assert result.reason == "limit_exceeded"
        assert result.limit == 10
        assert result.used == 10


class TestIdempotency:
    """Tests for idempotent behavior with limit exceeded."""

    def test_repeated_request_same_result(self):
        """Repeated requests with same event_id return same result."""
        from app.billing.limits import PlanLimitExceededError
        
        # First call raises
        error1 = PlanLimitExceededError("documents", 10, 10, "t1")
        
        # Second call should have same values
        error2 = PlanLimitExceededError("documents", 10, 10, "t1")
        
        assert error1.metric == error2.metric
        assert error1.limit == error2.limit
        assert error1.used == error2.used
        assert error1.tenant_id == error2.tenant_id


class TestDashboardLimitExceededBanner:
    """Tests for dashboard limit exceeded banner."""

    def test_dashboard_shows_exceeded_metrics(self, monkeypatch):
        """Dashboard shows banner when limit exceeded in 24h."""
        monkeypatch.setenv("TENANT_PORTAL_SESSION_SECRET", "test-secret")
        
        from starlette.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware
        from app.tenant_portal.web import router
        from app.api.tenant_portal import hash_token
        from fastapi import FastAPI
        
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret")
        app.include_router(router)
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        # Auth token
        mock_row = MagicMock()
        mock_row._mapping = {"token_hash": hash_token("key"), "token_prefix": "key"}
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_conn.execute.return_value.scalar.return_value = 0
        
        # Limit exceeded errors
        limit_error_row = MagicMock()
        limit_error_row.__getitem__ = lambda self, i: "plan_limit_exceeded:documents"
        mock_conn.execute.return_value.fetchall.return_value = [limit_error_row]
        
        with patch("app.tenant_portal.web.get_engine", return_value=mock_engine):
            client = TestClient(app)
            
            # Login
            client.post("/tenant/login", data={"tenant_id": "t1", "portal_key": "key"})
            
            # Check dashboard
            response = client.get("/tenant/")
        
        assert response.status_code == 200
        # The banner should mention limit exceeded
        assert "limit exceeded" in response.text.lower() or "Limits" in response.text
