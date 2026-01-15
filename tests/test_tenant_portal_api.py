"""Tests for Tenant Portal API v1 with per-tenant tokens."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# Disable metrics for tests
@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestTokenHelpers:
    """Tests for token generation and hashing."""

    def test_generate_portal_token_length(self):
        """Generated token has appropriate length."""
        from app.api.tenant_portal import generate_portal_token
        
        token = generate_portal_token()
        assert len(token) >= 32

    def test_hash_token_deterministic(self):
        """Hashing same token gives same result."""
        from app.api.tenant_portal import hash_token
        
        token = "test-token-123"
        hash1 = hash_token(token)
        hash2 = hash_token(token)
        
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex

    def test_hash_token_different_for_different_tokens(self):
        """Different tokens give different hashes."""
        from app.api.tenant_portal import hash_token
        
        hash1 = hash_token("token1")
        hash2 = hash_token("token2")
        
        assert hash1 != hash2


class TestTenantPortalAuth:
    """Tests for tenant portal authentication."""

    @pytest.fixture
    def client(self, monkeypatch):
        """Create test client."""
        from starlette.testclient import TestClient
        from app.api.tenant_portal import router
        from fastapi import FastAPI
        
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        return TestClient(app)

    def test_missing_portal_key_header_returns_403(self, client):
        """Returns 403 when X-Tenant-Portal-Key header is missing."""
        with patch("app.api.tenant_portal.get_engine"):
            response = client.get(
                "/api/v1/tenant/subscription",
                headers={"X-Tenant-ID": "t1"},
            )
        
        assert response.status_code == 403

    def test_missing_tenant_id_returns_400(self, client):
        """Returns 400 when X-Tenant-ID is missing."""
        with patch("app.api.tenant_portal.get_engine"):
            response = client.get(
                "/api/v1/tenant/subscription",
                headers={"X-Tenant-Portal-Key": "any"},
            )
        
        assert response.status_code == 400

    def test_no_active_token_returns_403(self, client):
        """Returns 403 when no active token exists for tenant."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchone.return_value = None
        
        with patch("app.api.tenant_portal.get_engine", return_value=mock_engine):
            response = client.get(
                "/api/v1/tenant/subscription",
                headers={"X-Tenant-ID": "t1", "X-Tenant-Portal-Key": "any"},
            )
        
        assert response.status_code == 403

    def test_invalid_token_returns_403(self, client):
        """Returns 403 when token doesn't match."""
        from app.api.tenant_portal import hash_token
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        # Return a token with different hash
        mock_row = MagicMock()
        mock_row._mapping = {"token_hash": hash_token("correct-token")}
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        
        with patch("app.api.tenant_portal.get_engine", return_value=mock_engine):
            response = client.get(
                "/api/v1/tenant/subscription",
                headers={"X-Tenant-ID": "t1", "X-Tenant-Portal-Key": "wrong-token"},
            )
        
        assert response.status_code == 403

    def test_db_error_returns_503(self, client):
        """Returns 503 on database error (fail-closed)."""
        with patch("app.api.tenant_portal.get_engine") as mock_engine:
            mock_engine.side_effect = Exception("DB down")
            
            response = client.get(
                "/api/v1/tenant/subscription",
                headers={"X-Tenant-ID": "t1", "X-Tenant-Portal-Key": "any"},
            )
        
        assert response.status_code == 503


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

    def test_auth_db_error_returns_503(self):
        """Auth returns 503 on DB error (fail-closed)."""
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
        
        # Fail-closed: 503 on DB error during auth
        assert response.status_code == 503
