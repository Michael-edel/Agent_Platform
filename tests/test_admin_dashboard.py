"""Tests for admin dashboard."""

import pytest
from unittest.mock import MagicMock, patch


class TestBuildDashboardStats:
    """Tests for build_dashboard_stats function."""

    def test_tenant_admin_scoping_applies_filter(self):
        """tenant_admin role applies tenant_id filter to queries."""
        from app.admin.dashboard import build_dashboard_stats
        
        # Mock the engine and connection
        mock_conn = MagicMock()
        mock_conn.execute.return_value.scalar.return_value = 5
        
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        with patch("app.admin.dashboard.get_engine", return_value=mock_engine):
            stats = build_dashboard_stats(role="tenant_admin", tenant_id="tenant-123")
        
        # Should have made 4 queries (tenants, subscriptions, orders, webhooks)
        assert mock_conn.execute.call_count == 4
        assert stats["scoped"] is True
        
        # Check that queries contain WHERE clause with tenant_id
        for call in mock_conn.execute.call_args_list:
            query = call[0][0]
            compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
            # Each query should filter by tenant_id
            assert "tenant_id" in compiled.lower()

    def test_tenant_admin_without_tenant_id_returns_zeros(self):
        """tenant_admin without tenant_id returns zero stats without error."""
        from app.admin.dashboard import build_dashboard_stats
        
        # Should not even call the engine
        with patch("app.admin.dashboard.get_engine") as mock_get_engine:
            stats = build_dashboard_stats(role="tenant_admin", tenant_id=None)
        
        # Engine should not be called
        mock_get_engine.assert_not_called()
        
        # All stats should be zero
        assert stats["tenants_count"] == 0
        assert stats["subscriptions_count"] == 0
        assert stats["orders_count"] == 0
        assert stats["webhooks_count"] == 0
        assert stats["scoped"] is True

    def test_platform_admin_no_scoping(self):
        """platform_admin sees all data without filtering."""
        from app.admin.dashboard import build_dashboard_stats
        
        mock_conn = MagicMock()
        mock_conn.execute.return_value.scalar.return_value = 10
        
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        with patch("app.admin.dashboard.get_engine", return_value=mock_engine):
            stats = build_dashboard_stats(role="platform_admin", tenant_id=None)
        
        assert stats["scoped"] is False
        assert stats["tenants_count"] == 10
        
        # Queries should NOT have tenant_id filter
        for call in mock_conn.execute.call_args_list:
            query = call[0][0]
            compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
            # Should not have WHERE tenant_id = ... clause
            assert "tenant_id =" not in compiled.lower() or "tenant_id" not in compiled

    def test_no_role_returns_zeros(self):
        """No role (deny by default) returns zero stats."""
        from app.admin.dashboard import build_dashboard_stats
        
        with patch("app.admin.dashboard.get_engine") as mock_get_engine:
            stats = build_dashboard_stats(role=None, tenant_id=None)
        
        mock_get_engine.assert_not_called()
        
        assert stats["tenants_count"] == 0
        assert stats["subscriptions_count"] == 0
        assert stats["orders_count"] == 0
        assert stats["webhooks_count"] == 0
        assert stats["role"] == "unknown"

    def test_db_error_returns_zeros(self):
        """Database error returns zeros without raising."""
        from app.admin.dashboard import build_dashboard_stats
        
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("DB connection failed")
        
        with patch("app.admin.dashboard.get_engine", return_value=mock_engine):
            stats = build_dashboard_stats(role="platform_admin", tenant_id=None)
        
        assert stats["tenants_count"] == 0
        assert stats["subscriptions_count"] == 0
