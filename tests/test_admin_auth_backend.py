import pytest
from starlette.requests import Request


@pytest.mark.anyio
async def test_admin_auth_authenticate_redirects_when_not_logged_in():
    from app.admin.auth import AdminAuthBackend

    backend = AdminAuthBackend(secret_key="test")
    scope = {"type": "http", "method": "GET", "path": "/admin", "headers": []}
    scope["session"] = {}
    req = Request(scope)

    resp = await backend.authenticate(req)
    assert resp is not None
    assert resp.status_code == 302
    assert resp.headers.get("location") == "/admin/login"


@pytest.mark.anyio
async def test_admin_auth_authenticate_allows_when_logged_in():
    from app.admin.auth import AdminAuthBackend

    backend = AdminAuthBackend(secret_key="test")
    scope = {"type": "http", "method": "GET", "path": "/admin", "headers": []}
    scope["session"] = {"admin_logged_in": True}
    req = Request(scope)

    resp = await backend.authenticate(req)
    assert resp is None


@pytest.mark.anyio
async def test_admin_login_does_not_log_password(caplog, monkeypatch):
    from app.admin.auth import AdminAuthBackend

    backend = AdminAuthBackend(secret_key="test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "expected")
    monkeypatch.setenv("ADMIN_ROLE", "platform_admin")

    class DummyRequest:
        def __init__(self):
            self.session = {}

        async def form(self):
            return {"username": "admin", "password": "super-secret-password"}

    with caplog.at_level("INFO"):
        ok = await backend.login(DummyRequest())  # type: ignore[arg-type]
    assert ok is False  # password mismatch

    # Ensure we never log the password value or a "password=" style string.
    text = caplog.text.lower()
    assert "super-secret-password" not in text
    assert "password=" not in text

"""Tests for admin authentication backend."""

import pytest
import asyncio
import logging
from unittest.mock import MagicMock, AsyncMock


def run_async(coro):
    """Helper to run async functions in sync tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestAdminAuthBackend:
    """Tests for AdminAuthBackend."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock request with session."""
        request = MagicMock()
        request.session = {}
        return request

    def test_login_rejects_invalid_password(self, mock_request, monkeypatch):
        """Login fails with wrong password."""
        monkeypatch.setenv("ADMIN_USERNAME", "admin")
        monkeypatch.setenv("ADMIN_PASSWORD", "correct_password")
        
        from app.admin.auth import AdminAuthBackend
        
        mock_request.form = AsyncMock(return_value={
            "username": "admin",
            "password": "wrong_password"
        })
        
        backend = AdminAuthBackend(secret_key="test")
        result = run_async(backend.login(mock_request))
        
        assert result is False
        assert mock_request.session.get("admin_logged_in") is None

    def test_login_accepts_valid_credentials(self, mock_request, monkeypatch):
        """Login succeeds with correct credentials."""
        monkeypatch.setenv("ADMIN_USERNAME", "admin")
        monkeypatch.setenv("ADMIN_PASSWORD", "secret123")
        monkeypatch.setenv("ADMIN_ROLE", "platform_admin")
        
        from app.admin.auth import AdminAuthBackend
        
        mock_request.form = AsyncMock(return_value={
            "username": "admin",
            "password": "secret123"
        })
        
        backend = AdminAuthBackend(secret_key="test")
        result = run_async(backend.login(mock_request))
        
        assert result is True
        assert mock_request.session.get("admin_logged_in") is True
        assert mock_request.session.get("admin_role") == "platform_admin"

    def test_login_denies_when_password_not_set(self, mock_request, monkeypatch):
        """Login fails if ADMIN_PASSWORD is not set."""
        monkeypatch.setenv("ADMIN_USERNAME", "admin")
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        
        from app.admin.auth import AdminAuthBackend
        
        mock_request.form = AsyncMock(return_value={
            "username": "admin",
            "password": "anything"
        })
        
        backend = AdminAuthBackend(secret_key="test")
        result = run_async(backend.login(mock_request))
        
        assert result is False

    def test_logout_clears_session(self, mock_request):
        """Logout clears the session."""
        mock_request.session = {
            "admin_logged_in": True,
            "admin_role": "platform_admin"
        }
        
        from app.admin.auth import AdminAuthBackend
        
        backend = AdminAuthBackend(secret_key="test")
        result = run_async(backend.logout(mock_request))
        
        assert result is True
        assert len(mock_request.session) == 0

    def test_password_not_logged(self, mock_request, monkeypatch, caplog):
        """Ensure password is never logged."""
        monkeypatch.setenv("ADMIN_USERNAME", "admin")
        monkeypatch.setenv("ADMIN_PASSWORD", "super_secret_password_12345")
        
        from app.admin.auth import AdminAuthBackend
        
        mock_request.form = AsyncMock(return_value={
            "username": "admin",
            "password": "wrong_password_attempt"
        })
        
        backend = AdminAuthBackend(secret_key="test")
        
        with caplog.at_level(logging.DEBUG):
            run_async(backend.login(mock_request))
        
        log_text = caplog.text
        assert "super_secret_password_12345" not in log_text
        assert "wrong_password_attempt" not in log_text

    def test_tenant_admin_stores_tenant_id(self, mock_request, monkeypatch):
        """Tenant admin role stores tenant_id in session."""
        monkeypatch.setenv("ADMIN_USERNAME", "admin")
        monkeypatch.setenv("ADMIN_PASSWORD", "secret")
        monkeypatch.setenv("ADMIN_ROLE", "tenant_admin")
        monkeypatch.setenv("ADMIN_TENANT_ID", "tenant-123")
        
        from app.admin.auth import AdminAuthBackend
        
        mock_request.form = AsyncMock(return_value={
            "username": "admin",
            "password": "secret"
        })
        
        backend = AdminAuthBackend(secret_key="test")
        result = run_async(backend.login(mock_request))
        
        assert result is True
        assert mock_request.session.get("admin_role") == "tenant_admin"
        assert mock_request.session.get("admin_tenant_id") == "tenant-123"


class TestGetAdminRole:
    """Tests for get_admin_role helper."""

    def test_returns_role_from_session(self):
        """Returns role stored in session."""
        from app.admin.auth import get_admin_role
        
        request = MagicMock()
        request.session = {"admin_role": "tenant_admin"}
        
        assert get_admin_role(request) == "tenant_admin"

    def test_returns_none_when_role_not_set(self):
        """Returns None if role not in session (deny by default)."""
        from app.admin.auth import get_admin_role
        
        request = MagicMock()
        request.session = {}
        
        assert get_admin_role(request) is None

    def test_returns_none_for_corrupted_session(self):
        """Returns None for session without admin_role key."""
        from app.admin.auth import get_admin_role
        
        request = MagicMock()
        request.session = {"some_other_key": "value"}
        
        assert get_admin_role(request) is None


class TestGetAdminTenantId:
    """Tests for get_admin_tenant_id helper."""

    def test_returns_tenant_id_for_tenant_admin(self):
        """Returns tenant_id for tenant_admin role."""
        from app.admin.auth import get_admin_tenant_id
        
        request = MagicMock()
        request.session = {
            "admin_role": "tenant_admin",
            "admin_tenant_id": "tenant-456"
        }
        
        assert get_admin_tenant_id(request) == "tenant-456"

    def test_returns_none_for_platform_admin(self):
        """Returns None for platform_admin role."""
        from app.admin.auth import get_admin_tenant_id
        
        request = MagicMock()
        request.session = {
            "admin_role": "platform_admin",
            "admin_tenant_id": "tenant-456"  # Should be ignored
        }
        
        assert get_admin_tenant_id(request) is None
