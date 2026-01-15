"""Tests for Tenant Portal API v1."""

import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# Disable metrics for tests
@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestTenantPortalAuth:
    """Tests for tenant portal authentication."""

    @pytest.fixture
    def client(self, monkeypatch):
        """Create test client."""
        monkeypatch.setenv("TENANT_PORTAL_KEY", "")  # Not configured
        from starlette.testclient import TestClient
        from app.api.tenant_portal import router
        from fastapi import FastAPI
        
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        return TestClient(app)

    def test_no_portal_key_configured_returns_503(self, client, monkeypatch):
        """Returns 503 when TENANT_PORTAL_KEY is not configured."""
        monkeypatch.setenv("TENANT_PORTAL_KEY", "")
        
        response = client.get(
            "/api/v1/tenant/subscription",
            headers={"X-Tenant-ID": "t1", "X-Tenant-Portal-Key": "any"},
        )
        
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]

    def test_missing_portal_key_header_returns_403(self, client, monkeypatch):
        """Returns 403 when X-Tenant-Portal-Key header is missing."""
        monkeypatch.setenv("TENANT_PORTAL_KEY", "secret123")
        
        response = client.get(
            "/api/v1/tenant/subscription",
            headers={"X-Tenant-ID": "t1"},
        )
        
        assert response.status_code == 403

    def test_invalid_portal_key_returns_403(self, client, monkeypatch):
        """Returns 403 when X-Tenant-Portal-Key is invalid."""
        monkeypatch.setenv("TENANT_PORTAL_KEY", "secret123")
        
        response = client.get(
            "/api/v1/tenant/subscription",
            headers={"X-Tenant-ID": "t1", "X-Tenant-Portal-Key": "wrong"},
        )
        
        assert response.status_code == 403

    def test_missing_tenant_id_returns_400(self, client, monkeypatch):
        """Returns 400 when X-Tenant-ID is missing."""
        monkeypatch.setenv("TENANT_PORTAL_KEY", "secret123")
        
        response = client.get(
            "/api/v1/tenant/subscription",
            headers={"X-Tenant-Portal-Key": "secret123"},
        )
        
        assert response.status_code == 400


class TestTenantPortalSchemas:
    """Tests for response schemas."""

    def test_subscription_response_has_required_fields(self):
        """SubscriptionResponse has all required fields."""
        from app.api.tenant_portal import SubscriptionResponse
        
        resp = SubscriptionResponse(tenant_id="t1")
        assert resp.tenant_id == "t1"
        assert resp.plan_id is None
        assert resp.subscription_status is None
        assert resp.provider is None

    def test_usage_response_has_required_fields(self):
        """UsageResponse has all required fields."""
        from app.api.tenant_portal import UsageResponse
        
        resp = UsageResponse(tenant_id="t1", period="2026-01", totals=[])
        assert resp.tenant_id == "t1"
        assert resp.period == "2026-01"
        assert resp.totals == []

    def test_status_response_has_required_fields(self):
        """StatusResponse has all required fields."""
        from app.api.tenant_portal import StatusResponse
        
        resp = StatusResponse(
            tenant_id="t1",
            webhooks_failed_24h=0,
            orders_failed_24h=0,
            status="ok",
        )
        assert resp.tenant_id == "t1"
        assert resp.status == "ok"


class TestTenantPortalWithMockedDb:
    """Tests with mocked database."""

    def test_subscription_graceful_on_db_error(self, monkeypatch):
        """Subscription endpoint handles DB errors gracefully."""
        monkeypatch.setenv("TENANT_PORTAL_KEY", "test-key")
        
        from starlette.testclient import TestClient
        from app.api.tenant_portal import router
        from fastapi import FastAPI
        
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        
        with patch("app.api.tenant_portal.get_engine") as mock_engine:
            mock_engine.side_effect = Exception("DB down")
            client = TestClient(app)
            response = client.get(
                "/api/v1/tenant/subscription",
                headers={"X-Tenant-ID": "t1", "X-Tenant-Portal-Key": "test-key"},
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] == "t1"
        assert data["plan_id"] is None

    def test_usage_graceful_on_db_error(self, monkeypatch):
        """Usage endpoint handles DB errors gracefully."""
        monkeypatch.setenv("TENANT_PORTAL_KEY", "test-key")
        
        from starlette.testclient import TestClient
        from app.api.tenant_portal import router
        from fastapi import FastAPI
        
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        
        with patch("app.api.tenant_portal.get_engine") as mock_engine:
            mock_engine.side_effect = Exception("DB down")
            client = TestClient(app)
            response = client.get(
                "/api/v1/tenant/usage",
                headers={"X-Tenant-ID": "t1", "X-Tenant-Portal-Key": "test-key"},
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] == "t1"
        assert data["totals"] == []

    def test_status_returns_unknown_on_db_error(self, monkeypatch):
        """Status endpoint returns 'unknown' on DB errors."""
        monkeypatch.setenv("TENANT_PORTAL_KEY", "test-key")
        
        from starlette.testclient import TestClient
        from app.api.tenant_portal import router
        from fastapi import FastAPI
        
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        
        with patch("app.api.tenant_portal.get_engine") as mock_engine:
            mock_engine.side_effect = Exception("DB down")
            client = TestClient(app)
            response = client.get(
                "/api/v1/tenant/status",
                headers={"X-Tenant-ID": "t1", "X-Tenant-Portal-Key": "test-key"},
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] == "t1"
        assert data["status"] == "unknown"
