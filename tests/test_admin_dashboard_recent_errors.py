"""Tests for admin dashboard recent errors section."""

import pytest
from unittest.mock import MagicMock, patch


class TestBuildRecentErrors:
    """Tests for build_recent_errors function."""

    def test_no_role_returns_empty(self):
        """No role returns empty errors."""
        from app.admin.dashboard import build_recent_errors
        
        result = build_recent_errors(None, None)
        
        assert result["webhook_errors"] == []
        assert result["order_errors"] == []

    def test_tenant_admin_without_tenant_id_returns_empty(self):
        """tenant_admin without tenant_id returns empty (fail-closed)."""
        from app.admin.dashboard import build_recent_errors
        
        result = build_recent_errors("tenant_admin", None)
        
        assert result["webhook_errors"] == []
        assert result["order_errors"] == []

    def test_graceful_error_handling(self):
        """Database errors are handled gracefully."""
        with patch("app.admin.dashboard.get_engine") as mock_engine:
            mock_engine.side_effect = Exception("DB connection failed")
            
            from app.admin.dashboard import build_recent_errors
            result = build_recent_errors("platform_admin", None)
        
        assert result["webhook_errors"] == []
        assert result["order_errors"] == []

    def test_returns_dict_with_required_keys(self):
        """Result has required keys."""
        with patch("app.admin.dashboard.get_engine") as mock_engine:
            mock_engine.side_effect = Exception("Test")
            
            from app.admin.dashboard import build_recent_errors
            result = build_recent_errors("platform_admin", None)
        
        assert "webhook_errors" in result
        assert "order_errors" in result


class TestTruncateError:
    """Tests for _truncate_error helper."""

    def test_truncates_long_text(self):
        """Long text is truncated."""
        from app.admin.dashboard import _truncate_error
        
        long_text = "a" * 200
        result = _truncate_error(long_text, 100)
        
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")

    def test_keeps_short_text(self):
        """Short text is kept as-is."""
        from app.admin.dashboard import _truncate_error
        
        result = _truncate_error("short error", 100)
        
        assert result == "short error"

    def test_handles_none(self):
        """None returns empty string."""
        from app.admin.dashboard import _truncate_error
        
        assert _truncate_error(None) == ""
        assert _truncate_error("") == ""

    def test_escapes_html(self):
        """HTML is escaped."""
        from app.admin.dashboard import _truncate_error
        
        result = _truncate_error("<script>alert(1)</script>")
        
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
