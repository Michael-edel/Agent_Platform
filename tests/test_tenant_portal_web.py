"""Tests for Tenant Portal Web UI."""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestPortalDisabled:
    """Tests when portal session secret is not configured."""

    def test_login_returns_503_when_not_configured(self, monkeypatch):
        """Login page returns 503 when session secret not set."""
        monkeypatch.setenv("TENANT_PORTAL_SESSION_SECRET", "")
        
        from starlette.testclient import TestClient
        from app.tenant_portal.web import router
        from fastapi import FastAPI
        
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        
        response = client.get("/tenant/login")
        assert response.status_code == 503


class TestPortalEnabled:
    """Tests when portal is properly configured."""

    @pytest.fixture
    def client(self, monkeypatch):
        """Create test client with session middleware."""
        monkeypatch.setenv("TENANT_PORTAL_SESSION_SECRET", "test-secret-key-123")
        
        from starlette.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware
        from app.tenant_portal.web import router
        from fastapi import FastAPI
        
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret-key-123")
        app.include_router(router)
        
        return TestClient(app)

    def test_dashboard_redirects_without_session(self, client):
        """Dashboard redirects to login when not authenticated."""
        response = client.get("/tenant/", follow_redirects=False)
        assert response.status_code == 302
        assert "/tenant/login" in response.headers["location"]

    def test_usage_redirects_without_session(self, client):
        """Usage page redirects to login when not authenticated."""
        response = client.get("/tenant/usage", follow_redirects=False)
        assert response.status_code == 302

    def test_subscription_redirects_without_session(self, client):
        """Subscription page redirects to login when not authenticated."""
        response = client.get("/tenant/subscription", follow_redirects=False)
        assert response.status_code == 302

    def test_login_page_renders(self, client):
        """Login page renders successfully."""
        response = client.get("/tenant/login")
        assert response.status_code == 200
        assert "Tenant Portal Login" in response.text

    def test_login_with_invalid_credentials(self, client):
        """Login with invalid credentials shows error."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchone.return_value = None
        
        with patch("app.tenant_portal.web.get_engine", return_value=mock_engine):
            response = client.post(
                "/tenant/login",
                data={"tenant_id": "t1", "portal_key": "wrong"},
            )
        
        assert response.status_code == 200
        assert "Invalid credentials" in response.text

    def test_login_success_redirects(self, client):
        """Successful login redirects to dashboard."""
        from app.api.tenant_portal import hash_token
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        # Return valid token
        mock_row = MagicMock()
        mock_row._mapping = {
            "token_hash": hash_token("correct-key"),
            "token_prefix": "correct-",
        }
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        
        with patch("app.tenant_portal.web.get_engine", return_value=mock_engine):
            response = client.post(
                "/tenant/login",
                data={"tenant_id": "t1", "portal_key": "correct-key"},
                follow_redirects=False,
            )
        
        assert response.status_code == 302
        assert "/tenant/" in response.headers["location"]

    def test_login_does_not_expose_key_in_response(self, client):
        """Login response does not contain the portal key."""
        from app.api.tenant_portal import hash_token
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        mock_row = MagicMock()
        mock_row._mapping = {
            "token_hash": hash_token("secret-key-xyz"),
            "token_prefix": "secret-k",
        }
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        
        with patch("app.tenant_portal.web.get_engine", return_value=mock_engine):
            # First login
            client.post(
                "/tenant/login",
                data={"tenant_id": "t1", "portal_key": "secret-key-xyz"},
            )
            
            # Then check dashboard
            with patch("app.tenant_portal.web.get_engine", return_value=mock_engine):
                mock_conn.execute.return_value.fetchone.return_value = None
                mock_conn.execute.return_value.scalar.return_value = 0
                
                response = client.get("/tenant/")
        
        # Key should not appear in response
        assert "secret-key-xyz" not in response.text

    def test_logout_clears_session(self, client):
        """Logout clears session and redirects to login."""
        response = client.post("/tenant/logout", follow_redirects=False)
        assert response.status_code == 302
        assert "/tenant/login" in response.headers["location"]

    def test_dashboard_shows_limits_section(self, client):
        """Dashboard shows limits section after login."""
        from app.api.tenant_portal import hash_token
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        # Login token
        mock_row = MagicMock()
        mock_row._mapping = {"token_hash": hash_token("key"), "token_prefix": "key"}
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_conn.execute.return_value.scalar.return_value = 0
        mock_conn.execute.return_value.fetchall.return_value = []
        
        with patch("app.tenant_portal.web.get_engine", return_value=mock_engine):
            # Login first
            client.post(
                "/tenant/login",
                data={"tenant_id": "t1", "portal_key": "key"},
            )
            
            # Access dashboard
            response = client.get("/tenant/")
        
        assert response.status_code == 200
        assert "Limits" in response.text
